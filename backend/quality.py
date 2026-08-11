"""Local, fast technical quality checks (no AI): sharpness, exposure, perceptual hash."""

import cv2
import imagehash
import numpy as np
from PIL import Image

_NORMALIZE_LONG_EDGE = 900
# Laplacian-variance value (on the normalized image) considered "tack sharp" -> maps to score 100
_SHARP_VARIANCE_CEILING = 800.0


def _normalize_for_analysis(img: Image.Image) -> np.ndarray:
    w, h = img.size
    scale = _NORMALIZE_LONG_EDGE / max(w, h)
    if scale < 1:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)


def sharpness_score(img: Image.Image) -> float:
    """0-100, higher = sharper. Based on variance of the Laplacian."""
    gray = _normalize_for_analysis(img)
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    score = 100.0 * min(1.0, variance / _SHARP_VARIANCE_CEILING)
    return round(score, 1)


def exposure_score(img: Image.Image) -> float:
    """0-100, higher = better exposed. Penalizes clipped highlights/shadows and poor mean brightness."""
    gray = _normalize_for_analysis(img)
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).flatten()
    total = hist.sum()
    if total == 0:
        return 0.0

    shadow_clip = hist[:5].sum() / total
    highlight_clip = hist[251:].sum() / total
    clip_penalty = (shadow_clip + highlight_clip) * 100

    mean_brightness = float(gray.mean())
    # ideal midtone brightness ~ 110-150; penalize distance outside that band
    if mean_brightness < 110:
        brightness_penalty = (110 - mean_brightness) / 110 * 40
    elif mean_brightness > 150:
        brightness_penalty = (mean_brightness - 150) / 105 * 40
    else:
        brightness_penalty = 0

    score = 100 - clip_penalty - brightness_penalty
    return round(float(max(0.0, min(100.0, score))), 1)


def perceptual_hash(img: Image.Image) -> str:
    return str(imagehash.phash(img, hash_size=8))


def hamming_distance(hash_a: str, hash_b: str) -> int:
    return imagehash.hex_to_hash(hash_a) - imagehash.hex_to_hash(hash_b)
