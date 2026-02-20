import io
import logging
import os

from telegram import Update
from telegram.ext import ContextTypes

from .authz import AuthzService
from .channels.telegram_signals import TelegramSignals
from .formatting import paginate_telegram
from .orchestrator import Orchestrator
from .rate_limit import SlidingWindowRateLimiter

logger = logging.getLogger(__name__)


class TelegramHandlers:
    def __init__(self, authz: AuthzService, orchestrator: Orchestrator):
        self.authz = authz
        self.orchestrator = orchestrator
        self.rate = SlidingWindowRateLimiter(max_events=15, window_seconds=60)
        self.max_image_mb = int(os.getenv("VISION_MAX_IMAGE_MB", "10"))
        self.max_audio_mb = int(os.getenv("AUDIO_MAX_MB", "20"))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.effective_message.reply_text(
            "Asistente activo. Usa /link <token> si no estas autorizado. Comandos: /prefs /forget /privacy"
        )

    async def link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return
        token = context.args[0] if context.args else ""
        if self.authz.try_link(user.id, token):
            await update.effective_message.reply_text("Usuario autorizado correctamente.")
        else:
            await update.effective_message.reply_text("Token invalido.")

    async def prefs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        msg = update.effective_message
        if not user or not msg:
            return

        raw = " ".join(context.args) if context.args else ""
        if "=" in raw:
            key, value = raw.split("=", 1)
            self.orchestrator.memory.set_pref(user.id, key.strip(), value.strip())
            await msg.reply_text(f"Preferencia guardada: {key.strip()}={value.strip()}")
            return

        prefs = self.orchestrator.memory.get_prefs(user.id)
        if not prefs:
            await msg.reply_text("No hay preferencias guardadas. Usa /prefs clave=valor")
            return
        lines = [f"- {k}: {v}" for k, v in prefs.items()]
        await msg.reply_text("Preferencias:\n" + "\n".join(lines))

    async def forget(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return
        self.orchestrator.memory.forget_user(user.id)
        await update.effective_message.reply_text("Memoria eliminada para tu user_id.")

    async def privacy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        txt = (
            "Guardamos memoria global por telegram_user_id: prefs, summary y memory items.\n"
            "Retencion configurable (MEMORY_RETENTION_DAYS).\n"
            "En grupos aplicamos politica anti-fuga.\n"
            "Puedes borrar todo con /forget"
        )
        await update.effective_message.reply_text(txt)

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if not msg or not user or not chat:
            return

        self.orchestrator.memory.upsert_user(user.id, chat.id, chat.type)

        if not self.authz.check_allowed(user.id, chat.id):
            await msg.reply_text("No autorizado. Usa /link <SHARE_TOKEN>.")
            return

        if not self.rate.allow(str(user.id)):
            await msg.reply_text("Rate limit alcanzado. Intenta de nuevo en un minuto.")
            return

        answer = None
        chart_png = None
        status_msg = None
        signals = TelegramSignals(context=context, chat_id=chat.id)
        try:
            if msg.photo:
                photo = msg.photo[-1]
                size_mb = (photo.file_size or 0) / (1024 * 1024)
                if size_mb > self.max_image_mb:
                    await msg.reply_text("Imagen demasiado grande.")
                    return
                status_msg = await msg.reply_text("Procesando imagen, espera un momento...")
                async with signals.typing():
                    file = await context.bot.get_file(photo.file_id)
                    image = await file.download_as_bytearray()
                    answer = await self.orchestrator.process_photo(
                        user.id,
                        chat.id,
                        chat.type,
                        bytes(image),
                        msg.caption,
                    )
            elif msg.voice or msg.audio:
                media = msg.voice or msg.audio
                size_mb = ((getattr(media, "file_size", None) or 0) / (1024 * 1024))
                if size_mb > self.max_audio_mb:
                    await msg.reply_text("Audio demasiado grande.")
                    return
                status_msg = await msg.reply_text("Transcribiendo audio...")
                file = await context.bot.get_file(media.file_id)
                audio_data = await file.download_as_bytearray()
                mime_type = getattr(media, "mime_type", None) or "audio/ogg"
                async with signals.typing():
                    answer = await self.orchestrator.process_audio(
                        user.id,
                        chat.id,
                        chat.type,
                        bytes(audio_data),
                        mime_type,
                        msg.caption,
                    )
            else:
                text = msg.text or ""
                async with signals.typing():
                    answer, chart_png = await self.orchestrator.process_text_with_media(
                        user.id,
                        chat.id,
                        chat.type,
                        text,
                    )
        except Exception:
            logger.exception("message_processing_failed")
            answer = "Hubo un error procesando tu mensaje. Intenta nuevamente."
        finally:
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    logger.debug("status_delete_error", exc_info=True)

        for chunk in paginate_telegram(answer or "No pude generar respuesta."):
            await msg.reply_text(chunk)
        if msg.text and chart_png:
            bio = io.BytesIO(chart_png)
            bio.name = "reporte_ventas.png"
            await context.bot.send_photo(chat_id=chat.id, photo=bio, caption="Grafico PNG del reporte")
