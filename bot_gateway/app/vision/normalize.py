import re


GTIN_RE = re.compile(r"\b\d{8,14}\b")
SKU_RE = re.compile(r"\b[A-Z0-9\-_]{4,24}\b", re.IGNORECASE)


def _is_likely_sku(value: str) -> bool:
    if len(value) < 5:
        return False
    return any(ch.isdigit() for ch in value) or "-" in value or "_" in value


def normalize_candidates(barcode: str | None, qr_text: str | None, ocr_text: str | None) -> tuple[str | None, list[str]]:
    raw_barcode = (barcode or "").strip()
    if raw_barcode:
        digits = re.sub(r"\D", "", raw_barcode)
        if 8 <= len(digits) <= 14:
            return digits, []

    text_blob = " ".join([x for x in [raw_barcode, qr_text, ocr_text] if x])
    gtins = GTIN_RE.findall(text_blob)
    if gtins:
        return gtins[0], []

    skus = []
    seen = set()
    for match in SKU_RE.findall(text_blob):
        up = match.upper()
        if up not in seen and _is_likely_sku(up):
            seen.add(up)
            skus.append(up)
    return None, skus[:5]
