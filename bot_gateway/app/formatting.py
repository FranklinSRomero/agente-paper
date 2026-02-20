import json


MAX_TOOL_TEXT = 3000


def sanitize_for_llm(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=True, default=str)
    compact = " ".join(raw.split())
    return compact[:MAX_TOOL_TEXT]


def paginate_telegram(text: str, limit: int = 3500) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    current = []
    size = 0
    for line in text.splitlines(keepends=True):
        if size + len(line) > limit and current:
            chunks.append("".join(current))
            current = [line]
            size = len(line)
        else:
            current.append(line)
            size += len(line)
    if current:
        chunks.append("".join(current))
    return chunks
