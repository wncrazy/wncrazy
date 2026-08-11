import os
from pathlib import Path
from typing import Iterator

from PIL import Image, ExifTags

from . import config

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass

_EXIF_TAGS = {v: k for k, v in ExifTags.TAGS.items()}
_DATETIME_TAG = _EXIF_TAGS.get("DateTimeOriginal")
_MODEL_TAG = _EXIF_TAGS.get("Model")


def iter_image_files(folder: str) -> Iterator[Path]:
    root = Path(folder)
    for dirpath, dirnames, filenames in os.walk(root):
        # skip our own culled/rejected output folders to avoid re-processing them
        dirnames[:] = [d for d in dirnames if d not in {"_rejected", "_culled", ".photo_culler"}]
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() in config.SUPPORTED_EXTS:
                yield p


def read_metadata(path: Path) -> dict:
    with Image.open(path) as img:
        width, height = img.size
        taken_at = None
        camera = None
        try:
            exif = img.getexif()
            if exif:
                if _DATETIME_TAG and _DATETIME_TAG in exif:
                    taken_at = str(exif[_DATETIME_TAG])
                if _MODEL_TAG and _MODEL_TAG in exif:
                    camera = str(exif[_MODEL_TAG]).strip()
        except Exception:
            pass
    stat = path.stat()
    return {
        "width": width,
        "height": height,
        "taken_at": taken_at,
        "camera": camera,
        "mtime": stat.st_mtime,
        "size": stat.st_size,
    }


def load_rgb(path: Path) -> Image.Image:
    img = Image.open(path)
    img = img.convert("RGB")
    try:
        from PIL import ImageOps

        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    return img
