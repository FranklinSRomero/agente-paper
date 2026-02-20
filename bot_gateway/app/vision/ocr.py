import pytesseract


def extract_text(image) -> str:
    text = pytesseract.image_to_string(image)
    return " ".join(text.split())
