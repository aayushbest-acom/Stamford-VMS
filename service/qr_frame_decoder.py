"""Loopback-only camera frame decoder using OpenCV's QRCodeDetector.

The browser-native BarcodeDetector API is missing in some Edge installations.
This module provides an on-device fallback: the browser posts downscaled JPEG
frames to the local VMS service and only the decoded QR text is returned.
Frames and token contents are never persisted or logged.
"""
from __future__ import annotations

import base64
import binascii
import re

MAX_FRAME_BYTES = 750_000
_DATA_URL_RE = re.compile(r"^data:image/(?:jpeg|png);base64,([A-Za-z0-9+/]+={0,2})$")


class QRDecoderUnavailable(RuntimeError):
    """Raised when the optional OpenCV decoder dependency is unavailable."""


def decode_qr_data_url(image_data: object) -> str | None:
    """Decode one base64 JPEG/PNG data URL. Returns QR text or None.

    Only small raster images are accepted. No image bytes are written to disk.
    """
    if not isinstance(image_data, str):
        raise ValueError("INVALID_IMAGE_DATA")
    match = _DATA_URL_RE.fullmatch(image_data.strip())
    if not match:
        raise ValueError("INVALID_IMAGE_DATA")
    encoded = match.group(1)
    # Bound encoded bytes before allocating a decoded image buffer.
    if len(encoded) > ((MAX_FRAME_BYTES + 2) // 3) * 4:
        raise ValueError("IMAGE_TOO_LARGE")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("INVALID_IMAGE_DATA") from exc
    if not raw or len(raw) > MAX_FRAME_BYTES:
        raise ValueError("IMAGE_TOO_LARGE" if raw else "INVALID_IMAGE_DATA")

    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # installation is handled by the Windows launcher
        raise QRDecoderUnavailable from exc

    buffer = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_GRAYSCALE)
    if image is None or image.size == 0:
        raise ValueError("INVALID_IMAGE_DATA")
    height, width = image.shape[:2]
    if width < 80 or height < 80 or width > 1600 or height > 1200:
        raise ValueError("IMAGE_DIMENSIONS_INVALID")

    detector = cv2.QRCodeDetector()
    text, _points, _straight = detector.detectAndDecode(image)
    if text and text.strip():
        return text.strip()

    # Multi-decode handles some frames containing more than one visible QR.
    multi = getattr(detector, "detectAndDecodeMulti", None)
    if callable(multi):
        try:
            result = multi(image)
            if result and result[0]:
                for candidate in result[1] or ():
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()
        except Exception:
            # A failed multi-decode is just a frame with no usable QR.
            pass
    return None
