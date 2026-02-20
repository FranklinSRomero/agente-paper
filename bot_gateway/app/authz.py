import os

from .memory.store import MemoryStore


def _parse_csv_ints(raw: str) -> set[int]:
    out = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


class AuthzService:
    def __init__(self, memory: MemoryStore):
        self.memory = memory
        self.allowed_users = _parse_csv_ints(os.getenv("TELEGRAM_ALLOWED_USER_IDS", ""))
        self.allowed_chats = _parse_csv_ints(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", ""))
        self.share_token = os.getenv("SHARE_TOKEN", "")

    def check_allowed(self, user_id: int, chat_id: int) -> bool:
        if user_id in self.allowed_users or chat_id in self.allowed_chats:
            return True
        return self.memory.is_user_authorized(user_id)

    def try_link(self, user_id: int, token: str) -> bool:
        if not self.share_token or token != self.share_token:
            return False
        self.memory.set_authorized(user_id, True)
        return True
