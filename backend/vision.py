"""Sends photos to a local Ollama vision model and parses a structured quality judgement."""

import base64
import io
import json
import re

import ollama
from PIL import Image

from . import config

_PROMPT = """You are a professional photo editor culling a shoot. Look at this single photo and \
judge it purely on technical and aesthetic quality for keep/reject decisions.

Respond with ONLY a JSON object (no markdown, no extra text) with exactly these fields:
{
  "sharpness": <0-10 integer, focus/motion blur on the main subject>,
  "composition": <0-10 integer, framing/subject placement>,
  "eyes_open": <true, false, or null if no faces are visible>,
  "expression": <0-10 integer, how good expressions/moments are; 10 if not applicable>,
  "overall": <0-10 integer, your overall keep-worthiness>,
  "flags": [<short strings from: "blurry", "closed_eyes", "bad_lighting", "awkward_expression", "great_shot">],
  "reason": "<one short sentence explaining the overall score>"
}"""


class VisionError(RuntimeError):
    pass


def _encode_image(img: Image.Image) -> bytes:
    img = img.copy()
    img.thumbnail(config.AI_INPUT_SIZE, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _extract_json(text: str) -> dict:
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise VisionError(f"No JSON object found in model response: {text[:200]!r}")
    return json.loads(match.group(0))


def score_photo(img: Image.Image, model: str | None = None, host: str | None = None) -> dict:
    """Returns {'ai_score': 0-100, 'ai_reason': str, 'ai_flags': list[str]}."""
    client = ollama.Client(host=host or config.OLLAMA_HOST)
    image_bytes = _encode_image(img)

    try:
        response = client.chat(
            model=model or config.OLLAMA_VISION_MODEL,
            messages=[{"role": "user", "content": _PROMPT, "images": [image_bytes]}],
            format="json",
            options={"temperature": 0.1},
        )
    except Exception as exc:  # connection errors, model missing, etc.
        raise VisionError(f"Ollama request failed: {exc}") from exc

    content = response["message"]["content"]
    try:
        data = _extract_json(content)
    except (ValueError, json.JSONDecodeError) as exc:
        raise VisionError(f"Could not parse model JSON: {exc}") from exc

    overall = data.get("overall")
    if overall is None:
        raise VisionError(f"Model response missing 'overall' field: {data}")

    ai_score = max(0.0, min(100.0, float(overall) * 10))
    flags = data.get("flags") or []
    if not isinstance(flags, list):
        flags = [str(flags)]

    return {
        "ai_score": round(ai_score, 1),
        "ai_reason": str(data.get("reason", ""))[:500],
        "ai_flags": [str(f) for f in flags][:10],
        "eyes_open": data.get("eyes_open"),
    }


def list_available_models(host: str | None = None) -> list[str]:
    client = ollama.Client(host=host or config.OLLAMA_HOST)
    try:
        result = client.list()
        return [m.get("model") or m.get("name") for m in result.get("models", [])]
    except Exception as exc:
        raise VisionError(f"Could not reach Ollama at {host or config.OLLAMA_HOST}: {exc}") from exc
