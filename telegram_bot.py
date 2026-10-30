# telegram_bot.py
# Tsclp_v2.1_volatile — Telegram-бот с watchdog для polling.
#
# WATCHDOG: каждые 60 сек проверяет, что polling реально получает обновления.
# При 3 неудачах подряд — перезапускает updater (лечит "тихое зависание" PTB).

import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import logger as research_logger


class TelegramBot:
    def __init__(self, token, chat_id, allowed_user_id, app_controls):
        self.application = (
            Application.builder()
            .token(token)
            .get_updates_read_timeout(60.0)
            .get_updates_connect_timeout(15.0)
            .get_updates_pool_timeout(10.0)
            .read_timeout(30.0)
            .connect_timeout(10.0)
            .pool_timeout(5.0)
            .build()
        )
        self.chat_id = chat_id
        try:
            self.allowed_user_id = int(allowed_user_id) if allowed_user_id else None
        except (TypeError, ValueError):
            self.allowed_user_id = None
        if self.allowed_user_id is None:
            logging.warning("TELEGRAM_ALLOWED_USER_ID не задан — команды будут отклонены.")
        self.app_controls = app_controls
        self._watchdog_task = None

        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("stop", self.stop_command))
        self.application.add_handler(CommandHandler("status", self.status_command))

    def _authorized(self, update: Update) -> bool:
        user = update.effective_user
        if user is None:
            return False
        ok = self.allowed_user_id is not None and user.id == self.allowed_user_id
        research_logger.log_event("TG_INCOMING", {
            "user_id": user.id if user else None,
            "username": getattr(user, "username", None) if user else None,
            "text": (update.message.text if update.message else "")[:200],
            "authorized": ok,
        })
        return ok

    async def _deny(self, update: Update):
        try:
            await update.message.reply_text("⛔ Нет прав.")
        except Exception:
            pass

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            await self._deny(update)
            return
        self.app_controls['start_trading']()
        await update.message.reply_text("✅ Новые входы разрешены (если нет блокировки).")

    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            await self._deny(update)
            return
        self.app_controls['stop_trading']()
        await update.message.reply_text("🛑 Новые входы запрещены. Позиции/мониторинг продолжаются.")

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            await self._deny(update)
            return
        await update.message.reply_text(self.app_controls['get_status']())

    async def send_message(self, text: str):
        try:
            await self.application.bot.send_message(
                chat_id=self.chat_id, text=text, parse_mode='HTML'
            )
        except Exception as e:
            logging.error(f"Telegram send error: {e}")

    async def _watchdog_loop(self):
        """Проверяет живость polling. При 3 неудачах подряд — перезапуск updater."""
        failures = 0
        while True:
            await asyncio.sleep(60)
            try:
                # Лёгкий запрос: get_me. Если polling завис, он может не отвечать.
                await asyncio.wait_for(self.application.bot.get_me(), timeout=15.0)
                failures = 0
            except Exception as e:
                failures += 1
                logging.warning(f"[watchdog] getUpdates check failed ({failures}/3): {e}")
                if failures >= 3:
                    logging.error("[watchdog] Polling завис. Перезапуск updater...")
                    try:
                        if self.application.updater.running:
                            await self.application.updater.stop()
                        await self.application.updater.start_polling()
                        logging.info("[watchdog] Updater перезапущен.")
                    except Exception as restart_err:
                        logging.error(f"[watchdog] Не удалось перезапустить: {restart_err}")
                    failures = 0

    def start_watchdog(self):
        """Запускает watchdog как фоновую задачу в текущем event loop."""
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())
            logging.info("[watchdog] Telegram watchdog запущен (проверка каждые 60 сек).")