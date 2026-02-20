from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters

from ..telegram_handlers import TelegramHandlers


class TelegramChannelAdapter:
    def __init__(self, token: str, handlers: TelegramHandlers):
        self.token = token
        self.handlers = handlers
        self.app: Application | None = None

    async def start(self) -> Application:
        self.app = ApplicationBuilder().token(self.token).build()
        self.app.add_handler(CommandHandler("start", self.handlers.start))
        self.app.add_handler(CommandHandler("link", self.handlers.link))
        self.app.add_handler(CommandHandler("prefs", self.handlers.prefs))
        self.app.add_handler(CommandHandler("forget", self.handlers.forget))
        self.app.add_handler(CommandHandler("privacy", self.handlers.privacy))
        self.app.add_handler(
            MessageHandler(filters.TEXT | filters.PHOTO | filters.VOICE | filters.AUDIO, self.handlers.on_message)
        )

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        return self.app

    async def stop(self) -> None:
        if not self.app:
            return
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
