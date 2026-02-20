from .models import UserMemoryItem


def filter_memory_for_chat(items: list[UserMemoryItem], chat_type: str, strict_group: bool) -> list[UserMemoryItem]:
    if chat_type == "private" or not strict_group:
        return items

    allowed = []
    for item in items:
        if item.kind == "preference":
            allowed.append(item)
            continue
        # Group/supergroup: block private-origin details to avoid leakage.
        if item.source_chat_type in ("group", "supergroup"):
            allowed.append(item)
    return allowed
