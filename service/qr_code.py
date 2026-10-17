"""QR rendering wrapper for the vendored BSD-licensed qrcode runtime."""
from __future__ import annotations
import sys
from pathlib import Path

_VENDOR = str(Path(__file__).resolve().parent / "vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)


def make_svg(token: str) -> bytes:
    from io import BytesIO
    import qrcode
    from qrcode.image.svg import SvgPathImage

    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4, image_factory=SvgPathImage)
    qr.add_data(token)
    qr.make(fit=True)
    image = qr.make_image()
    output = BytesIO()
    image.save(output)
    return output.getvalue()
