import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from telegram.constants import ChatAction
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


class TelegramSignals:
    def __init__(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
        self.context = context
        self.chat_id = chat_id
        self._task: asyncio.Task | None = None

    async def _keep_chat_action(self, action: str) -> None:
        try:
            while True:
                await self.context.bot.send_chat_action(chat_id=self.chat_id, action=action)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("chat_action_error", exc_info=True)

    @asynccontextmanager
    async def typing(self) -> AsyncIterator[None]:
        self._task = asyncio.create_task(self._keep_chat_action(ChatAction.TYPING))
        try:
            yield
        finally:
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None
