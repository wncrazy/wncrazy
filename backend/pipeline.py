"""Orchestrates a folder scan: metadata -> technical quality -> duplicate grouping -> AI scoring."""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image

from . import config, db, scanner, quality, dedupe, vision

log = logging.getLogger("photo_culler.pipeline")


def _get_or_create_photo_row(conn, path: Path, meta: dict) -> tuple[int, bool]:
    """Returns (photo_id, needs_reprocessing)."""
    row = conn.execute("SELECT id, mtime, size FROM photos WHERE path = ?", (str(path),)).fetchone()
    if row is None:
        cur = conn.execute(
            """INSERT INTO photos (path, filename, mtime, size, width, height, taken_at, camera,
                                    status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                str(path), path.name, meta["mtime"], meta["size"], meta["width"], meta["height"],
                meta.get("taken_at"), meta.get("camera"), time.time(),
            ),
        )
        return cur.lastrowid, True

    unchanged = row["mtime"] == meta["mtime"] and row["size"] == meta["size"]
    if not unchanged:
        conn.execute(
            """UPDATE photos SET mtime=?, size=?, width=?, height=?, taken_at=?, camera=?,
                                  status='pending', error=NULL, updated_at=?
               WHERE id=?""",
            (meta["mtime"], meta["size"], meta["width"], meta["height"], meta.get("taken_at"),
             meta.get("camera"), time.time(), row["id"]),
        )
    return row["id"], not unchanged


def _process_quality(photo_id: int, path: Path):
    conn = db.get_conn()
    try:
        img = scanner.load_rgb(path)
        sharpness = quality.sharpness_score(img)
        exposure = quality.exposure_score(img)
        phash = quality.perceptual_hash(img)
        is_blurry = 1 if sharpness < config.BLUR_FLAG_THRESHOLD else 0
        conn.execute(
            """UPDATE photos SET sharpness_score=?, exposure_score=?, phash=?, is_blurry=?,
                                  status='quality_done', updated_at=? WHERE id=?""",
            (sharpness, exposure, phash, is_blurry, time.time(), photo_id),
        )
        conn.commit()
        _make_thumbnail(photo_id, path, img)
    except Exception as exc:
        log.exception("quality check failed for %s", path)
        conn.execute(
            "UPDATE photos SET status='error', error=?, updated_at=? WHERE id=?",
            (f"quality: {exc}", time.time(), photo_id),
        )
        conn.commit()


def _make_thumbnail(photo_id: int, path: Path, img=None):
    thumb_path = config.THUMB_DIR / f"{photo_id}.jpg"
    if thumb_path.exists():
        return
    if img is None:
        img = scanner.load_rgb(path)
    thumb = img.copy()
    thumb.thumbnail(config.THUMB_SIZE, Image.LANCZOS)
    thumb.save(thumb_path, format="JPEG", quality=85)


def _process_ai(photo_id: int, path: Path):
    conn = db.get_conn()
    try:
        img = scanner.load_rgb(path)
        result = vision.score_photo(img)
        conn.execute(
            """UPDATE photos SET ai_score=?, ai_reason=?, ai_flags=?, status='ai_done',
                                  updated_at=? WHERE id=?""",
            (result["ai_score"], result["ai_reason"], json.dumps(result["ai_flags"]),
             time.time(), photo_id),
        )
        conn.commit()
    except vision.VisionError as exc:
        log.warning("AI scoring unavailable for %s: %s", path, exc)
        conn.execute(
            "UPDATE photos SET status='ai_unavailable', error=?, updated_at=? WHERE id=?",
            (str(exc), time.time(), photo_id),
        )
        conn.commit()
    except Exception as exc:
        log.exception("AI scoring failed for %s", path)
        conn.execute(
            "UPDATE photos SET status='error', error=?, updated_at=? WHERE id=?",
            (f"ai: {exc}", time.time(), photo_id),
        )
        conn.commit()


def _combined_score(row: dict) -> float:
    sharpness = row["sharpness_score"] or 0
    exposure = row["exposure_score"] or 0
    ai = row["ai_score"]
    if ai is None:
        # AI unavailable: redistribute its weight across the technical checks
        total_w = config.WEIGHT_SHARPNESS + config.WEIGHT_EXPOSURE
        return round((sharpness * config.WEIGHT_SHARPNESS + exposure * config.WEIGHT_EXPOSURE) / total_w, 1)
    return round(
        ai * config.WEIGHT_AI + sharpness * config.WEIGHT_SHARPNESS + exposure * config.WEIGHT_EXPOSURE, 1
    )


def _finalize_scores_and_groups(folder: str):
    conn = db.get_conn()
    rows = [
        db.row_to_dict(r)
        for r in conn.execute(
            "SELECT * FROM photos WHERE path LIKE ? AND status != 'error'", (f"{folder}%",)
        ).fetchall()
    ]

    groups = dedupe.group_duplicates(rows)

    best_in_group: dict[int, tuple[int, float, int]] = {}  # group_id -> (photo_id, score, resolution)
    for row in rows:
        row["combined_score"] = _combined_score(row)
        gid = groups.get(row["id"])
        if gid is not None:
            current = best_in_group.get(gid)
            resolution = (row["width"] or 0) * (row["height"] or 0)
            if current is None or (row["combined_score"], resolution) > (current[1], current[2]):
                best_in_group[gid] = (row["id"], row["combined_score"], resolution)

    for row in rows:
        gid = groups.get(row["id"])
        is_best = 1 if gid is not None and best_in_group.get(gid, (None,))[0] == row["id"] else 0
        conn.execute(
            """UPDATE photos SET combined_score=?, duplicate_group_id=?, is_best_in_group=?,
                                  status='done', updated_at=? WHERE id=?""",
            (row["combined_score"], gid, is_best, time.time(), row["id"]),
        )
    conn.commit()


def run_scan(scan_id: int, folder: str, use_ai: bool = True):
    conn = db.get_conn()
    conn.execute("UPDATE scans SET status='listing' WHERE id=?", (scan_id,))
    conn.commit()

    try:
        files = list(scanner.iter_image_files(folder))
    except Exception as exc:
        conn.execute(
            "UPDATE scans SET status='error', error=?, finished_at=? WHERE id=?",
            (str(exc), time.time(), scan_id),
        )
        conn.commit()
        return

    conn.execute("UPDATE scans SET total=?, status='metadata' WHERE id=?", (len(files), scan_id))
    conn.commit()

    todo: list[tuple[int, Path]] = []
    for path in files:
        try:
            meta = scanner.read_metadata(path)
        except Exception as exc:
            log.warning("could not read metadata for %s: %s", path, exc)
            continue
        photo_id, needs_reprocessing = _get_or_create_photo_row(conn, path, meta)
        conn.commit()
        if needs_reprocessing:
            todo.append((photo_id, path))

    conn.execute("UPDATE scans SET status='quality', processed=0 WHERE id=?", (scan_id,))
    conn.commit()

    with ThreadPoolExecutor(max_workers=max(4, (os.cpu_count() or 4))) as pool:
        futures = [pool.submit(_process_quality, pid, p) for pid, p in todo]
        for i, _ in enumerate(as_completed(futures), start=1):
            conn.execute("UPDATE scans SET processed=? WHERE id=?", (i, scan_id))
            conn.commit()

    if use_ai and todo:
        conn.execute(
            "UPDATE scans SET status='ai_scoring', processed=0, total=? WHERE id=?", (len(todo), scan_id)
        )
        conn.commit()
        with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_AI_CALLS) as pool:
            futures = [pool.submit(_process_ai, pid, p) for pid, p in todo]
            for i, _ in enumerate(as_completed(futures), start=1):
                conn.execute("UPDATE scans SET processed=? WHERE id=?", (i, scan_id))
                conn.commit()

    conn.execute("UPDATE scans SET status='finalizing' WHERE id=?", (scan_id,))
    conn.commit()
    _finalize_scores_and_groups(folder)

    conn.execute(
        "UPDATE scans SET status='done', finished_at=? WHERE id=?", (time.time(), scan_id)
    )
    conn.commit()
