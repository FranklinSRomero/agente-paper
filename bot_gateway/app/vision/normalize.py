import re


GTIN_RE = re.compile(r"(?<!\d)\d{8,14}(?!\d)")
GTIN_FUZZY_RE = re.compile(r"(?:\d[\s\-]*){8,14}")
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
    compact_blob = re.sub(r"[^0-9A-Z\-_]", "", text_blob)
    compact_gtins = GTIN_RE.findall(compact_blob)
    if compact_gtins:
        return compact_gtins[0], []
    fuzzy = GTIN_FUZZY_RE.findall(text_blob)
    for candidate in fuzzy:
        digits = re.sub(r"\D", "", candidate)
        if 8 <= len(digits) <= 14:
            return digits, []

    skus = []
    seen = set()
    for match in SKU_RE.findall(text_blob):
        up = match.upper()
        if up not in seen and _is_likely_sku(up):
            seen.add(up)
            skus.append(up)
    return None, skus[:5]
