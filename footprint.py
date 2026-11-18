"""
footprint.py — Cryptographic, Perceptual, and Metadata Footprinting for Images & Files.

Generates:
- Cryptographic hashes (MD5, SHA-1, SHA-256, SHA-512)
- Perceptual image hashes (pHash via 2D DCT, dHash, aHash)
- Dominant color signature
- EXIF, GPS (decimal lat/lon), PNG text chunks, and file magic-byte metadata
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any

import numpy as np
from PIL import ExifTags, Image, ImageOps

try:
    import cv2  # type: ignore

    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False


MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"%PDF", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
]


def detect_mime_type(path: Path) -> str:
    """Detect MIME type from magic bytes, falling back to file extension."""
    try:
        with path.open("rb") as f:
            header = f.read(16)
        for sig, mime in MAGIC_SIGNATURES:
            if header.startswith(sig):
                if sig == b"RIFF" and b"WEBP" not in header:
                    continue
                return mime
    except OSError:
        pass
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def compute_file_hashes(path: Path) -> dict[str, Any]:
    """Compute MD5, SHA-1, SHA-256, SHA-512 and byte size for any file."""
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()
    sha512 = hashlib.sha512()
    size = 0

    with path.open("rb") as f:
        while chunk := f.read(65536):
            size += len(chunk)
            md5.update(chunk)
            sha1.update(chunk)
            sha256.update(chunk)
            sha512.update(chunk)

    return {
        "size_bytes": size,
        "md5": md5.hexdigest(),
        "sha1": sha1.hexdigest(),
        "sha256": sha256.hexdigest(),
        "sha512": sha512.hexdigest(),
    }


def _bits_to_hex(bits: np.ndarray) -> str:
    """Convert a 1D boolean/0-1 numpy array (length multiple of 4) to hex."""
    flat = bits.astype(np.uint8).flatten()
    byte_len = (len(flat) + 7) // 8
    packed = np.packbits(flat)
    return bytes(packed[:byte_len]).hex()


def hamming_distance(hex_a: str, hex_b: str) -> int:
    """Compute Hamming distance between two hex hash strings of equal length."""
    if not hex_a or not hex_b or len(hex_a) != len(hex_b):
        return 64
    xor_val = int(hex_a, 16) ^ int(hex_b, 16)
    return xor_val.bit_count()


def similarity_percent(distance: int, total_bits: int = 64) -> float:
    """Convert Hamming distance to a 0-100% similarity score."""
    clamped = max(0, min(distance, total_bits))
    return round((1.0 - clamped / total_bits) * 100.0, 2)


def compute_ahash(img: Image.Image, hash_size: int = 8) -> str:
    """Average Hash (aHash): resilient to scaling and minor aspect changes."""
    gray = ImageOps.exif_transpose(img).convert("L").resize(
        (hash_size, hash_size), Image.Resampling.LANCZOS
    )
    pixels = np.asarray(gray, dtype=np.float32)
    mean_val = pixels.mean()
    return _bits_to_hex(pixels > mean_val)


def compute_dhash(img: Image.Image, hash_size: int = 8) -> str:
    """Difference Hash (dHash): tracks horizontal gradients; fast & robust."""
    gray = ImageOps.exif_transpose(img).convert("L").resize(
        (hash_size + 1, hash_size), Image.Resampling.LANCZOS
    )
    pixels = np.asarray(gray, dtype=np.float32)
    diff = pixels[:, 1:] > pixels[:, :-1]
    return _bits_to_hex(diff)


def _dct_2d_numpy(matrix: np.ndarray) -> np.ndarray:
    """Pure-NumPy 2D Type-II DCT fallback when OpenCV is unavailable."""
    n, m = matrix.shape
    k_n = np.arange(n)[:, None]
    x_n = np.arange(n)[None, :]
    c_n = np.cos(np.pi * (2 * x_n + 1) * k_n / (2 * n))

    k_m = np.arange(m)[:, None]
    x_m = np.arange(m)[None, :]
    c_m = np.cos(np.pi * (2 * x_m + 1) * k_m / (2 * m))

    return c_n @ matrix @ c_m.T


def compute_phash(img: Image.Image, hash_size: int = 8, highfreq_factor: int = 4) -> str:
    """
    Perceptual Hash (pHash) via 2D Discrete Cosine Transform.
    Resilient to JPEG compression, resizing, gamma shifts, and light watermarks.
    """
    img_size = hash_size * highfreq_factor
    gray = ImageOps.exif_transpose(img).convert("L").resize(
        (img_size, img_size), Image.Resampling.LANCZOS
    )
    pixels = np.asarray(gray, dtype=np.float32)

    if _HAS_CV2:
        dct = cv2.dct(pixels)
    else:
        dct = _dct_2d_numpy(pixels)

    dct_low = dct[:hash_size, :hash_size]
    flat = dct_low.flatten()
    median_val = float(np.median(flat[1:])) if len(flat) > 1 else float(np.median(flat))
    return _bits_to_hex(dct_low > median_val)


def compute_dominant_colors(img: Image.Image, num_colors: int = 5) -> list[str]:
    """Return top dominant hex colors using median-cut quantization."""
    thumb = img.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR)
    quantized = thumb.quantize(colors=num_colors, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    counts = quantized.getcolors() or []
    counts.sort(key=lambda x: x[0], reverse=True)

    colors: list[str] = []
    for _, idx in counts[:num_colors]:
        base = idx * 3
        if base + 2 < len(palette):
            r, g, b = palette[base], palette[base + 1], palette[base + 2]
            colors.append(f"#{r:02x}{g:02x}{b:02x}")
    return colors


def _dms_to_decimal(dms: Any, ref: str) -> float | None:
    """Convert EXIF GPS (deg, min, sec) tuple to decimal degrees."""
    try:
        d, m, s = [float(x) for x in dms]
        dec = d + m / 60.0 + s / 3600.0
        if ref in ("S", "W"):
            dec = -dec
        return round(dec, 6)
    except Exception:
        return None


def extract_image_metadata(img: Image.Image) -> dict[str, Any]:
    """Extract dimensions, format, EXIF tags, GPS coordinates, and PNG info."""
    meta: dict[str, Any] = {
        "format": img.format,
        "mode": img.mode,
        "width": img.width,
        "height": img.height,
        "has_icc_profile": "icc_profile" in img.info,
        "exif": {},
        "gps": None,
        "text_chunks": {},
    }

    for k, v in img.info.items():
        if k in ("icc_profile", "exif"):
            continue
        if isinstance(v, (str, int, float)):
            meta["text_chunks"][str(k)] = v

    try:
        raw_exif = img.getexif()
        if raw_exif:
            for tag_id, value in raw_exif.items():
                tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                if isinstance(value, bytes):
                    continue
                meta["exif"][tag_name] = str(value)

            gps_ifd = raw_exif.get_ifd(0x8825)
            if gps_ifd:
                gps_decoded = {
                    ExifTags.GPSTAGS.get(k, str(k)): v for k, v in gps_ifd.items()
                }
                lat = _dms_to_decimal(
                    gps_decoded.get("GPSLatitude"),
                    str(gps_decoded.get("GPSLatitudeRef", "N")),
                )
                lon = _dms_to_decimal(
                    gps_decoded.get("GPSLongitude"),
                    str(gps_decoded.get("GPSLongitudeRef", "E")),
                )
                if lat is not None and lon is not None:
                    meta["gps"] = {"latitude": lat, "longitude": lon}
    except Exception:
        pass

    return meta


def analyze_footprint(file_path: str | Path) -> dict[str, Any]:
    """
    Generate a complete footprint for any file (cryptographic + MIME) and,
    if it is a readable image, perceptual hashes, dominant colors, and EXIF.
    """
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    result: dict[str, Any] = {
        "file_name": path.name,
        "file_path": str(path),
        "mime_type": detect_mime_type(path),
        "crypto": compute_file_hashes(path),
        "is_image": False,
        "perceptual_hashes": None,
        "dominant_colors": [],
        "image_metadata": None,
    }

    try:
        with Image.open(path) as img:
            img.load()
            result["is_image"] = True
            result["perceptual_hashes"] = {
                "phash": compute_phash(img),
                "dhash": compute_dhash(img),
                "ahash": compute_ahash(img),
            }
            result["dominant_colors"] = compute_dominant_colors(img)
            result["image_metadata"] = extract_image_metadata(img)
    except Exception:
        pass

    return result
