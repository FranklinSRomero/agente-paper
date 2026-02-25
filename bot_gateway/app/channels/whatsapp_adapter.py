import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx

from ..authz import AuthzService
from ..formatting import paginate_telegram
from ..orchestrator import Orchestrator
from ..rate_limit import SlidingWindowRateLimiter

logger = logging.getLogger(__name__)


class WhatsAppCloudAdapter:
    def __init__(self, authz: AuthzService, orchestrator: Orchestrator):
        self.authz = authz
        self.orchestrator = orchestrator
        self.rate = SlidingWindowRateLimiter(max_events=15, window_seconds=60)

        self.enabled = os.getenv("BOT_ENABLE_WHATSAPP", "false").lower() == "true"
        self.graph_base = os.getenv("WHATSAPP_GRAPH_BASE_URL", "https://graph.facebook.com")
        # Keep this aligned with current stable Graph API for WhatsApp Cloud API.
        self.graph_version = os.getenv("WHATSAPP_GRAPH_VERSION", "v24.0")
        self.phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        self.access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
        self.verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()

        self.max_image_mb = int(os.getenv("VISION_MAX_IMAGE_MB", "10"))
        self.max_audio_mb = int(os.getenv("AUDIO_MAX_MB", "20"))
        self.typing_best_effort = os.getenv("WHATSAPP_TYPING_BEST_EFFORT", "true").lower() == "true"

    def is_configured(self) -> bool:
        if not self.enabled:
            return False
        return bool(self.phone_number_id and self.access_token and self.verify_token)

    def verify_webhook(self, mode: str | None, token: str | None, challenge: str | None) -> str | None:
        if mode == "subscribe" and token == self.verify_token and challenge:
            return challenge
        return None

    def _api_url(self, path: str) -> str:
        return f"{self.graph_base}/{self.graph_version}/{path.lstrip('/')}"

    async def _post_messages(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = self._api_url(f"{self.phone_number_id}/messages")
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                logger.warning("whatsapp_post_failed status=%s body=%s", resp.status_code, resp.text[:400])
                return {"error": f"status_{resp.status_code}", "body": resp.text[:400]}
            return resp.json()

    async def _send_text(self, to: str, text: str, reply_to_message_id: str | None = None) -> None:
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }
        if reply_to_message_id:
            payload["context"] = {"message_id": reply_to_message_id}
        await self._post_messages(payload)

    async def _mark_as_read(self, message_id: str) -> None:
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        await self._post_messages(payload)

    async def _typing_best_effort_read(self, message_id: str) -> None:
        if not self.typing_best_effort:
            return
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {"type": "text"},
        }
        await self._post_messages(payload)

    async def _download_media(self, media_id: str) -> tuple[bytes, str]:
        info_url = self._api_url(media_id)
        headers = {"Authorization": f"Bearer {self.access_token}"}
        timeout = httpx.Timeout(20.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            info_resp = await client.get(info_url, headers=headers)
            info_resp.raise_for_status()
            info = info_resp.json()
            download_url = info.get("url")
            mime_type = info.get("mime_type") or "application/octet-stream"
            if not download_url:
                raise RuntimeError("media_url_missing")

            media_resp = await client.get(download_url, headers=headers)
            media_resp.raise_for_status()
            return media_resp.content, mime_type

    @asynccontextmanager
    async def _status_context(self, message_id: str | None):
        if message_id:
            await self._mark_as_read(message_id)
            await self._typing_best_effort_read(message_id)
        try:
            yield
        finally:
            await asyncio.sleep(0)

    def _to_numeric_id(self, wa_id: str) -> int:
        digits = "".join(ch for ch in wa_id if ch.isdigit())
        if not digits:
            return abs(hash(wa_id)) % 2_000_000_000
        # Keep int range reasonable for persistence layer.
        return int(digits[-15:])

    async def _reply_chunks(self, wa_id: str, message_id: str | None, text: str) -> None:
        for chunk in paginate_telegram(text or "No pude generar respuesta.", limit=3500):
            await self._send_text(to=wa_id, text=chunk, reply_to_message_id=message_id)

    async def _handle_text(self, wa_id: str, message: dict[str, Any], user_id: int) -> None:
        message_id = message.get("id")
        text = ((message.get("text") or {}).get("body") or "").strip()
        chat_id = user_id
        chat_type = "private"

        self.orchestrator.memory.upsert_user(user_id, chat_id, chat_type)

        if text.lower().startswith("/link "):
            token = text.split(" ", 1)[1].strip()
            ok = self.authz.try_link(user_id, token)
            await self._reply_chunks(wa_id, message_id, "Usuario autorizado correctamente." if ok else "Token invalido.")
            return

        if text.lower() == "/privacy":
            privacy = (
                "Guardamos memoria global por user_id. Retencion configurable. "
                "Puedes borrar con /forget."
            )
            await self._reply_chunks(wa_id, message_id, privacy)
            return

        if text.lower() == "/forget":
            self.orchestrator.memory.forget_user(user_id)
            await self._reply_chunks(wa_id, message_id, "Memoria eliminada para tu user_id.")
            return

        if not self.authz.check_allowed(user_id, chat_id):
            await self._reply_chunks(wa_id, message_id, "No autorizado. Usa /link <SHARE_TOKEN>.")
            return

        if not self.rate.allow(f"wa:{user_id}"):
            await self._reply_chunks(wa_id, message_id, "Rate limit alcanzado. Intenta de nuevo en un minuto.")
            return

        async with self._status_context(message_id):
            answer, _chart_png = await self.orchestrator.process_text_with_media(user_id, chat_id, chat_type, text)
        await self._reply_chunks(wa_id, message_id, answer)

    async def _handle_image(self, wa_id: str, message: dict[str, Any], user_id: int) -> None:
        message_id = message.get("id")
        image = message.get("image") or {}
        media_id = image.get("id")
        caption = image.get("caption")
        if not media_id:
            await self._reply_chunks(wa_id, message_id, "No pude leer la imagen recibida.")
            return

        if not self.authz.check_allowed(user_id, user_id):
            await self._reply_chunks(wa_id, message_id, "No autorizado. Usa /link <SHARE_TOKEN>.")
            return

        try:
            media_bytes, _mime_type = await self._download_media(media_id)
        except Exception:
            logger.exception("whatsapp_image_download_failed")
            await self._reply_chunks(wa_id, message_id, "No pude descargar la imagen.")
            return

        size_mb = len(media_bytes) / (1024 * 1024)
        if size_mb > self.max_image_mb:
            await self._reply_chunks(wa_id, message_id, "Imagen demasiado grande.")
            return

        async with self._status_context(message_id):
            answer = await self.orchestrator.process_photo(user_id, user_id, "private", media_bytes, caption)
        await self._reply_chunks(wa_id, message_id, answer)

    async def _handle_audio(self, wa_id: str, message: dict[str, Any], user_id: int) -> None:
        message_id = message.get("id")
        audio = message.get("audio") or {}
        media_id = audio.get("id")
        if not media_id:
            await self._reply_chunks(wa_id, message_id, "No pude leer el audio recibido.")
            return

        if not self.authz.check_allowed(user_id, user_id):
            await self._reply_chunks(wa_id, message_id, "No autorizado. Usa /link <SHARE_TOKEN>.")
            return

        try:
            media_bytes, mime_type = await self._download_media(media_id)
        except Exception:
            logger.exception("whatsapp_audio_download_failed")
            await self._reply_chunks(wa_id, message_id, "No pude descargar el audio.")
            return

        size_mb = len(media_bytes) / (1024 * 1024)
        if size_mb > self.max_audio_mb:
            await self._reply_chunks(wa_id, message_id, "Audio demasiado grande.")
            return

        async with self._status_context(message_id):
            answer = await self.orchestrator.process_audio(user_id, user_id, "private", media_bytes, mime_type)
        await self._reply_chunks(wa_id, message_id, answer)

    async def handle_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "ignored", "reason": "whatsapp_not_configured"}

        messages_seen = 0
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value") or {}
                for message in value.get("messages", []) or []:
                    messages_seen += 1
                    wa_id = str(message.get("from") or "").strip()
                    if not wa_id:
                        continue
                    user_id = self._to_numeric_id(wa_id)
                    mtype = (message.get("type") or "").lower()

                    try:
                        if mtype == "text":
                            await self._handle_text(wa_id, message, user_id)
                        elif mtype == "image":
                            await self._handle_image(wa_id, message, user_id)
                        elif mtype in ("audio", "voice"):
                            await self._handle_audio(wa_id, message, user_id)
                        else:
                            await self._reply_chunks(
                                wa_id,
                                message.get("id"),
                                "Tipo de mensaje no soportado todavia. Puedes enviar texto, imagen o audio.",
                            )
                    except Exception:
                        logger.exception("whatsapp_message_handling_failed")
                        await self._reply_chunks(wa_id, message.get("id"), "Hubo un error procesando tu mensaje.")

        return {"status": "ok", "messages_seen": messages_seen}
