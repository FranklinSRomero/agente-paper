import logging

from telegram import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, MenuButtonCommands
from telegram.error import TelegramError
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from ..telegram_handlers import TelegramHandlers

logger = logging.getLogger(__name__)


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
        self.app.add_handler(CommandHandler("ayuda", self.handlers.ayuda))
        self.app.add_handler(CommandHandler("precio", self.handlers.precio))
        self.app.add_handler(CommandHandler("stock", self.handlers.stock))
        self.app.add_handler(CommandHandler("buscar", self.handlers.buscar))
        self.app.add_handler(CallbackQueryHandler(self.handlers.on_menu_callback, pattern=r"^menu:"))
        self.app.add_handler(CallbackQueryHandler(self.handlers.on_detail_callback, pattern=r"^detail:"))
        self.app.add_handler(CallbackQueryHandler(self.handlers.on_more_callback, pattern=r"^more:"))
        self.app.add_handler(
            MessageHandler(filters.TEXT | filters.PHOTO | filters.VOICE | filters.AUDIO, self.handlers.on_message)
        )

        await self.app.initialize()
        await self._configure_bot_commands()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        return self.app

    async def _configure_bot_commands(self) -> None:
        if not self.app:
            return
        commands = [
            BotCommand("start", "Inicia el bot y muestra el menu"),
            BotCommand("ayuda", "Muestra comandos disponibles"),
            BotCommand("precio", "Consulta precio por sku o codigo"),
            BotCommand("stock", "Consulta stock por sku o codigo"),
            BotCommand("buscar", "Busca productos por nombre, sku o categoria"),
            BotCommand("link", "Autoriza tu usuario con token"),
            BotCommand("prefs", "Ver o guardar preferencias"),
            BotCommand("forget", "Borra tu memoria"),
            BotCommand("privacy", "Ver politica de datos"),
        ]
        try:
            # Telegram command menu shown by "/" in private chats.
            await self.app.bot.set_my_commands(commands)
            await self.app.bot.set_my_commands(commands, language_code="es")
            await self.app.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
            await self.app.bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())
            # Forces the left menu button to open command list.
            await self.app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        except TelegramError:
            logger.exception("telegram_set_commands_failed")

    async def stop(self) -> None:
        if not self.app:
            return
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
