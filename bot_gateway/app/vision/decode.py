import cv2
import numpy as np
from pyzbar.pyzbar import decode as zbar_decode


BARCODE_TYPES = {
    "EAN13",
    "EAN8",
    "UPCA",
    "UPCE",
    "CODE128",
    "CODE39",
    "ITF",
    "CODABAR",
    "DATABAR",
    "DATABAR_EXP",
    "PDF417",
}


def _rotate_variants(img):
    return [
        img,
        cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE),
        cv2.rotate(img, cv2.ROTATE_180),
        cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE),
    ]


def _pixel_variants(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    adaptive = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        2,
    )
    enlarged = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    return [img, gray, clahe, otsu, adaptive, enlarged]


def _decode_with_variants(img) -> tuple[str | None, str | None]:
    qr_text = None
    for rotated in _rotate_variants(img):
        for candidate in _pixel_variants(rotated):
            decoded = zbar_decode(candidate)
            for item in decoded:
                text = item.data.decode("utf-8", errors="ignore").strip()
                if not text:
                    continue
                code_type = (item.type or "").upper()
                if code_type in BARCODE_TYPES:
                    return text, None
                if code_type == "QRCODE" and not qr_text:
                    qr_text = text
    return None, qr_text


def decode_barcode_or_qr(image_bytes: bytes) -> tuple[str | None, str | None]:
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None, None

    barcode, qr_text = _decode_with_variants(img)
    if barcode or qr_text:
        return barcode, qr_text

    detector = cv2.QRCodeDetector()
    qr_text, _, _ = detector.detectAndDecode(img)
    if qr_text:
        return None, qr_text
    return None, None
