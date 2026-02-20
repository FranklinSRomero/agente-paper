from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    last_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_chat_type: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Pref(Base):
    __tablename__ = "prefs"
    __table_args__ = (PrimaryKeyConstraint("user_id", "key"),)

    user_id: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(128))
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserSummary(Base):
    __tablename__ = "user_summary"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    summary_text: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    msg_count: Mapped[int] = mapped_column(Integer, default=0)


class UserMemoryItem(Base):
    __tablename__ = "user_memory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    source_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_chat_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def make_engine(db_path: str):
    return create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})


def make_session_factory(db_path: str):
    engine = make_engine(db_path)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)
