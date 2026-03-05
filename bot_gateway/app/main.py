import asyncio
import logging
import os

from fastapi import FastAPI

from .authz import AuthzService
from .channels.telegram_adapter import TelegramChannelAdapter
from .logging_conf import setup_logging
from .memory.store import MemoryStore
from .orchestrator import Orchestrator
from .telegram_handlers import TelegramHandlers

setup_logging()
logger = logging.getLogger(__name__)

memory = MemoryStore(
    db_path=os.getenv("MEMORY_DB_PATH", "/data/memory.db"),
    retention_days=int(os.getenv("MEMORY_RETENTION_DAYS", "365")),
)
authz = AuthzService(memory)
orchestrator = Orchestrator(memory)
handlers = TelegramHandlers(authz, orchestrator)

telegram_channel: TelegramChannelAdapter | None = None


def _telegram_enabled() -> bool:
    return os.getenv("BOT_ENABLE_TELEGRAM", "true").lower() == "true"


def _get_telegram_token() -> str:
    return (
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        or os.getenv("TELEGRAM_BOT_API", "").strip()
        or os.getenv("telegram_bot_api", "").strip()
    )


def _validate_startup_config() -> None:
    missing = []
    has_single = bool(os.getenv("GEMINI_API_KEY", "").strip())
    has_multi = bool(os.getenv("GEMINI_API_KEYS", "").strip())
    if not (has_single or has_multi):
        missing.append("GEMINI_API_KEY (or GEMINI_API_KEYS)")

    if _telegram_enabled():
        if not _get_telegram_token():
            missing.append("TELEGRAM_BOT_TOKEN (or TELEGRAM_BOT_API / telegram_bot_api)")
    else:
        missing.append("BOT_ENABLE_TELEGRAM=true")

    if missing:
        raise RuntimeError("Missing required startup configuration: " + ", ".join(missing))


async def run_polling() -> None:
    global telegram_channel
    if not _telegram_enabled():
        return
    token = _get_telegram_token()
    telegram_channel = TelegramChannelAdapter(token=token, handlers=handlers)
    await telegram_channel.start()


api = FastAPI(title="bot_gateway")


@api.on_event("startup")
async def on_startup() -> None:
    _validate_startup_config()
    api.state.polling_task = asyncio.create_task(run_polling())


@api.on_event("shutdown")
async def on_shutdown() -> None:
    task = getattr(api.state, "polling_task", None)
    if task:
        task.cancel()
    global telegram_channel
    if telegram_channel:
        await telegram_channel.stop()


@api.get("/health")
async def health() -> dict:
    return {"status": "ok"}
