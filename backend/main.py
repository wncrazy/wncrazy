import logging
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, pipeline, vision

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("photo_culler")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Photo Culler", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

LOW_SCORE_THRESHOLD = 45  # combined_score below this is suggested for rejection


# ---------- request/response models ----------

class ScanRequest(BaseModel):
    folder: str
    use_ai: bool = True
    model: str | None = None


class DecisionRequest(BaseModel):
    decision: str  # "keep" | "reject" | "unset"


class ApplyRequest(BaseModel):
    folder: str
    dest_subfolder: str = "_rejected"
    dry_run: bool = False


# ---------- helpers ----------

def _suggested_reject(row: dict) -> bool:
    if row.get("duplicate_group_id") is not None and not row.get("is_best_in_group"):
        return True
    if row.get("is_blurry"):
        return True
    score = row.get("combined_score")
    if score is not None and score < LOW_SCORE_THRESHOLD:
        return True
    return False


def _enrich(row: dict) -> dict:
    row["suggested_reject"] = _suggested_reject(row)
    row["effective_decision"] = (
        row["decision"] if row.get("decision") in ("keep", "reject")
        else ("reject" if row["suggested_reject"] else "keep")
    )
    return row


# ---------- scan ----------

@app.post("/api/scan")
def start_scan(req: ScanRequest):
    folder = Path(req.folder).expanduser()
    if not folder.is_dir():
        raise HTTPException(400, f"Folder not found: {folder}")
    if req.model:
        config.OLLAMA_VISION_MODEL = req.model

    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO scans (folder, started_at, status) VALUES (?, ?, 'queued')",
        (str(folder), time.time()),
    )
    conn.commit()
    scan_id = cur.lastrowid

    thread = threading.Thread(
        target=pipeline.run_scan, args=(scan_id, str(folder), req.use_ai), daemon=True
    )
    thread.start()

    return {"scan_id": scan_id, "folder": str(folder)}


@app.get("/api/scan/{scan_id}")
def scan_status(scan_id: int):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    if not row:
        raise HTTPException(404, "scan not found")
    return db.row_to_dict(row)


# ---------- photos ----------

@app.get("/api/photos")
def list_photos(folder: str, group_only: bool = False):
    folder = str(Path(folder).expanduser())
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM photos WHERE path LIKE ? ORDER BY duplicate_group_id, combined_score DESC",
        (f"{folder}%",),
    ).fetchall()
    photos = [_enrich(db.row_to_dict(r)) for r in rows]
    if group_only:
        photos = [p for p in photos if p["duplicate_group_id"] is not None]
    return {"photos": photos, "count": len(photos)}


@app.get("/api/photos/{photo_id}/thumbnail")
def get_thumbnail(photo_id: int):
    thumb_path = config.THUMB_DIR / f"{photo_id}.jpg"
    if not thumb_path.exists():
        raise HTTPException(404, "thumbnail not ready")
    return FileResponse(thumb_path, media_type="image/jpeg")


@app.get("/api/photos/{photo_id}/full")
def get_full(photo_id: int):
    conn = db.get_conn()
    row = conn.execute("SELECT path FROM photos WHERE id=?", (photo_id,)).fetchone()
    if not row:
        raise HTTPException(404, "photo not found")
    return FileResponse(row["path"])


@app.post("/api/photos/{photo_id}/decision")
def set_decision(photo_id: int, req: DecisionRequest):
    if req.decision not in ("keep", "reject", "unset"):
        raise HTTPException(400, "decision must be keep|reject|unset")
    conn = db.get_conn()
    cur = conn.execute("UPDATE photos SET decision=? WHERE id=?", (req.decision, photo_id))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "photo not found")
    return {"ok": True}


# ---------- apply culling ----------

@app.post("/api/apply")
def apply_culling(req: ApplyRequest):
    folder = Path(req.folder).expanduser()
    conn = db.get_conn()
    rows = [
        db.row_to_dict(r)
        for r in conn.execute(
            "SELECT * FROM photos WHERE path LIKE ?", (f"{folder}%",)
        ).fetchall()
    ]

    to_move = []
    for row in rows:
        row = _enrich(row)
        if row["effective_decision"] == "reject":
            to_move.append(row)

    moved = []
    for row in to_move:
        src = Path(row["path"])
        if not src.exists():
            continue
        rel = src.relative_to(folder)
        dest = folder / req.dest_subfolder / rel
        moved.append({"from": str(src), "to": str(dest)})
        if not req.dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            conn.execute("UPDATE photos SET path=?, status='moved' WHERE id=?", (str(dest), row["id"]))

    if not req.dry_run:
        conn.commit()

    return {"moved_count": len(moved), "moved": moved, "dry_run": req.dry_run}


# ---------- ollama status ----------

@app.get("/api/ollama/status")
def ollama_status():
    try:
        models = vision.list_available_models()
        return {"reachable": True, "models": models, "configured_model": config.OLLAMA_VISION_MODEL}
    except vision.VisionError as exc:
        return {"reachable": False, "error": str(exc), "configured_model": config.OLLAMA_VISION_MODEL}


@app.get("/api/config")
def get_config():
    return {
        "ollama_host": config.OLLAMA_HOST,
        "ollama_model": config.OLLAMA_VISION_MODEL,
        "low_score_threshold": LOW_SCORE_THRESHOLD,
    }


# ---------- static frontend ----------

_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
