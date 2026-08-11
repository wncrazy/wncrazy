import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("PHOTO_CULLER_DATA", Path.home() / ".photo_culler"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "library.db"
THUMB_DIR = DATA_DIR / "thumbnails"
THUMB_DIR.mkdir(parents=True, exist_ok=True)

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "gemma3:4b")

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp", ".tiff"}

THUMB_SIZE = (480, 480)
AI_INPUT_SIZE = (768, 768)

# Hamming distance (0-64) below which two images are considered near-duplicates
DUPLICATE_PHASH_THRESHOLD = 8

# Weights for the combined score (0-100)
WEIGHT_AI = 0.6
WEIGHT_SHARPNESS = 0.25
WEIGHT_EXPOSURE = 0.15

# Below this Laplacian-variance-derived sharpness score (0-100), a photo is flagged as blurry
BLUR_FLAG_THRESHOLD = 35

MAX_CONCURRENT_AI_CALLS = 2
