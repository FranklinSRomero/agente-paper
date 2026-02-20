import base64
import re

import cv2
import numpy as np
import pytesseract
from pyzbar.pyzbar import decode as zbar_decode


GTIN_RE = re.compile(r"\b\d{8,14}\b")
SKU_RE = re.compile(r"\b[A-Z0-9\-_]{4,24}\b", re.IGNORECASE)
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


def _is_likely_sku(value: str) -> bool:
    if len(value) < 5:
        return False
    return any(ch.isdigit() for ch in value) or "-" in value or "_" in value


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


def _decode_barcode_or_qr(img) -> tuple[str | None, str | None]:
    barcode, qr_text = _decode_with_variants(img)
    if barcode or qr_text:
        return barcode, qr_text

    detector = cv2.QRCodeDetector()
    qr_text, _, _ = detector.detectAndDecode(img)
    if qr_text:
        return None, qr_text
    return None, None


def _ocr(gray_img) -> str:
    h, w = gray_img.shape[:2]
    roi = gray_img[h // 2 :, :] if h >= 100 else gray_img
    roi = cv2.resize(roi, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    blur = cv2.GaussianBlur(roi, (3, 3), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    configs = [
        "--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
        "--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
        "--psm 11",
    ]
    parts = []
    for cfg in configs:
        txt = pytesseract.image_to_string(th, config=cfg)
        if txt:
            parts.append(" ".join(txt.split()))
    return " ".join(parts).strip()


def _normalize(barcode: str | None, qr_text: str | None, ocr_text: str | None):
    raw_barcode = (barcode or "").strip()
    if raw_barcode:
        digits = re.sub(r"\D", "", raw_barcode)
        if 8 <= len(digits) <= 14:
            return digits, []

    blob = " ".join([x for x in [raw_barcode, qr_text, ocr_text] if x])
    gtins = GTIN_RE.findall(blob)
    if gtins:
        return gtins[0], []

    skus = []
    seen = set()
    for match in SKU_RE.findall(blob):
        up = match.upper()
        if up not in seen and _is_likely_sku(up):
            seen.add(up)
            skus.append(up)
    return None, skus[:5]


def process_image_payload(image_b64: str) -> dict:
    raw = base64.b64decode(image_b64)
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return {"error": "invalid_image"}

    barcode, qr_text = _decode_barcode_or_qr(img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ocr_text = ""
    if not barcode and not qr_text:
        ocr_text = _ocr(gray)

    norm_barcode, sku_candidates = _normalize(barcode, qr_text, ocr_text)
    return {
        "barcode": norm_barcode,
        "qr_text": qr_text,
        "sku_candidates": sku_candidates,
        "ocr_text": ocr_text[:300],
    }
