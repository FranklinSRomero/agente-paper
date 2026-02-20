import asyncio
import logging
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse

from .authz import AuthzService
from .channels.telegram_adapter import TelegramChannelAdapter
from .channels.whatsapp_adapter import WhatsAppCloudAdapter
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
whatsapp_channel = WhatsAppCloudAdapter(authz, orchestrator)

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

    active_channels = 0
    if _telegram_enabled():
        active_channels += 1
        if not _get_telegram_token():
            missing.append("TELEGRAM_BOT_TOKEN (or TELEGRAM_BOT_API / telegram_bot_api)")
    if whatsapp_channel.enabled:
        active_channels += 1
        if not whatsapp_channel.is_configured():
            missing.append("WHATSAPP_PHONE_NUMBER_ID + WHATSAPP_ACCESS_TOKEN + WHATSAPP_VERIFY_TOKEN")
    if active_channels == 0:
        missing.append("Enable at least one channel (BOT_ENABLE_TELEGRAM or BOT_ENABLE_WHATSAPP)")

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


@api.get("/webhooks/whatsapp", response_class=PlainTextResponse)
async def whatsapp_verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> str:
    challenge = whatsapp_channel.verify_webhook(hub_mode, hub_verify_token, hub_challenge)
    if not challenge:
        raise HTTPException(status_code=403, detail="webhook verification failed")
    return challenge


@api.post("/webhooks/whatsapp")
async def whatsapp_webhook(payload: dict) -> dict:
    return await whatsapp_channel.handle_webhook(payload)
