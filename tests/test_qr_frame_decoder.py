import base64
from io import BytesIO
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "service"
if str(SERVICE / "vendor") not in sys.path:
    sys.path.insert(0, str(SERVICE / "vendor"))

from service.qr_frame_decoder import decode_qr_data_url, QRDecoderUnavailable

try:
    import cv2  # noqa: F401
    import numpy  # noqa: F401
    import qrcode
    HAS_DECODER = True
except Exception:
    HAS_DECODER = False


@unittest.skipUnless(HAS_DECODER, "OpenCV and Pillow are optional local scanner dependencies")
class QRFrameDecoderTests(unittest.TestCase):
    def test_decodes_camera_jpeg_data_url_locally(self):
        token = "VMS-SYNTHETIC-CAMERA-TOKEN-0123456789ABCDEF"
        from PIL import Image, ImageDraw
        qr = qrcode.QRCode(box_size=1, border=4)
        qr.add_data(token)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        scale = 8
        image = Image.new("RGB", (len(matrix) * scale, len(matrix) * scale), "white")
        draw = ImageDraw.Draw(image)
        for y, row in enumerate(matrix):
            for x, module in enumerate(row):
                if module:
                    draw.rectangle((x * scale, y * scale, (x + 1) * scale - 1, (y + 1) * scale - 1), fill="black")
        output = BytesIO()
        image.save(output, format="JPEG", quality=96)
        data_url = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
        self.assertEqual(decode_qr_data_url(data_url), token)

    def test_rejects_non_data_url(self):
        with self.assertRaises(ValueError):
            decode_qr_data_url("not-a-data-url")

    def test_rejects_oversized_frame(self):
        payload = base64.b64encode(b"x" * 760_000).decode("ascii")
        with self.assertRaises(ValueError):
            decode_qr_data_url("data:image/jpeg;base64," + payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
