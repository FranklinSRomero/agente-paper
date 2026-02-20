from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass(slots=True)
class IncomingMessage:
    platform: str
    user_id: int
    chat_id: int
    chat_type: str
    text: str | None = None
    caption: str | None = None


class PlatformSignals:
    @asynccontextmanager
    async def typing(self) -> AsyncIterator[None]:
        yield
