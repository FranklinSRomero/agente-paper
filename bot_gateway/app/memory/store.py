import json
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from .models import Pref, User, UserMemoryItem, UserSummary, make_session_factory


class MemoryStore:
    def __init__(self, db_path: str, retention_days: int = 365):
        self.session_factory = make_session_factory(db_path)
        self.retention_days = retention_days

    def upsert_user(self, user_id: int, chat_id: int, chat_type: str) -> User:
        with self.session_factory() as s:
            user = s.get(User, user_id)
            if not user:
                user = User(user_id=user_id)
                s.add(user)
            user.last_seen = datetime.utcnow()
            user.last_chat_id = chat_id
            user.last_chat_type = chat_type
            s.commit()
            s.refresh(user)
            return user

    def set_authorized(self, user_id: int, authorized: bool = True) -> None:
        with self.session_factory() as s:
            user = s.get(User, user_id)
            if not user:
                user = User(user_id=user_id, is_authorized=authorized)
                s.add(user)
            else:
                user.is_authorized = authorized
            s.commit()

    def is_user_authorized(self, user_id: int) -> bool:
        with self.session_factory() as s:
            user = s.get(User, user_id)
            return bool(user and user.is_authorized)

    def set_pref(self, user_id: int, key: str, value: str) -> None:
        with self.session_factory() as s:
            pref = s.get(Pref, (user_id, key))
            if not pref:
                pref = Pref(user_id=user_id, key=key, value=value)
                s.add(pref)
            else:
                pref.value = value
                pref.updated_at = datetime.utcnow()
            s.commit()

    def get_prefs(self, user_id: int) -> dict[str, str]:
        with self.session_factory() as s:
            rows = s.scalars(select(Pref).where(Pref.user_id == user_id)).all()
            return {r.key: r.value for r in rows}

    def add_memory_item(self, user_id: int, kind: str, content: str, chat_id: int, chat_type: str) -> None:
        with self.session_factory() as s:
            item = UserMemoryItem(
                user_id=user_id,
                kind=kind,
                content=content,
                source_chat_id=chat_id,
                source_chat_type=chat_type,
            )
            s.add(item)
            s.commit()

    def get_memory_items(self, user_id: int, limit: int = 30) -> list[UserMemoryItem]:
        with self.session_factory() as s:
            rows = s.scalars(
                select(UserMemoryItem)
                .where(UserMemoryItem.user_id == user_id)
                .order_by(UserMemoryItem.created_at.desc())
                .limit(limit)
            ).all()
            return rows

    def get_summary(self, user_id: int) -> UserSummary:
        with self.session_factory() as s:
            summary = s.get(UserSummary, user_id)
            if not summary:
                summary = UserSummary(user_id=user_id, summary_text="", msg_count=0)
                s.add(summary)
                s.commit()
                s.refresh(summary)
            return summary

    def update_summary(self, user_id: int, summary_text: str, msg_count: int) -> None:
        with self.session_factory() as s:
            summary = s.get(UserSummary, user_id)
            if not summary:
                summary = UserSummary(user_id=user_id)
                s.add(summary)
            summary.summary_text = summary_text
            summary.msg_count = msg_count
            summary.updated_at = datetime.utcnow()
            s.commit()

    def increment_msg_count(self, user_id: int) -> int:
        with self.session_factory() as s:
            summary = s.get(UserSummary, user_id)
            if not summary:
                summary = UserSummary(user_id=user_id, msg_count=0, summary_text="")
                s.add(summary)
            summary.msg_count += 1
            summary.updated_at = datetime.utcnow()
            s.commit()
            return summary.msg_count

    def forget_user(self, user_id: int) -> None:
        with self.session_factory() as s:
            s.execute(delete(Pref).where(Pref.user_id == user_id))
            s.execute(delete(UserMemoryItem).where(UserMemoryItem.user_id == user_id))
            s.execute(delete(UserSummary).where(UserSummary.user_id == user_id))
            s.commit()

    def cleanup_old(self) -> None:
        cutoff = datetime.utcnow() - timedelta(days=self.retention_days)
        with self.session_factory() as s:
            s.execute(delete(UserMemoryItem).where(UserMemoryItem.created_at < cutoff))
            s.commit()

    def export_user_state(self, user_id: int) -> dict:
        return {
            "prefs": self.get_prefs(user_id),
            "summary": self.get_summary(user_id).summary_text,
            "items": [
                {
                    "kind": i.kind,
                    "content": i.content,
                    "source_chat_type": i.source_chat_type,
                }
                for i in self.get_memory_items(user_id)
            ],
        }

    def prefs_as_text(self, user_id: int) -> str:
        return json.dumps(self.get_prefs(user_id), ensure_ascii=True)
