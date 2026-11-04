"""Telegram UX, ручной мониторинг и планировщик автоматической рассылки."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .auth import verify_secret_word
from .automation import StateStore, automation_enable_error, next_scheduled_run, normalize_schedule_time
from .channel import ChannelPublisher
from .config import Settings
from .email_delivery import send_email_report
from .recipients import RecipientError, normalize_email
from .report_service import generate_report_bundle
from .reporting import render_text
from .source_service import make_google_client
from .time_windows import INTERVAL_EXAMPLES, lookback_interval, parse_exact_interval, timezone


STATE_SECRET = "waiting_secret"
STATE_INTERVAL = "waiting_interval"
STATE_ADD_RECIPIENT = "waiting_recipient"
STATE_ADD_TIME = "waiting_schedule_time"


def split_telegram_text(text: str, limit: int = 3900) -> list[str]:
    if limit <= 0:
        raise ValueError("limit должен быть положительным")
    remaining = str(text or "")
    chunks: list[str] = []
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit + 1)
        cut = limit if cut <= 0 else cut + 1
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Сделать мониторинг", callback_data="monitoring")],
        [InlineKeyboardButton("✉️ Настроить письма", callback_data="email_settings")],
        [InlineKeyboardButton("📊 Состояние приложения", callback_data="status")],
    ])


def monitoring_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🕒 Последние 12 часов", callback_data="last12")],
        [InlineKeyboardButton("📅 Выбрать интервал", callback_data="interval")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="main")],
    ])


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="main")]])


def interval_help() -> str:
    return "Введите интервал в одном из форматов:\n" + "\n".join(INTERVAL_EXAMPLES)


def format_user_datetime(value: str | None, timezone_name: str) -> str:
    if not value:
        return "Ещё не выполнялось"
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone(timezone_name))
        return parsed.astimezone(timezone(timezone_name)).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return "Некорректная дата"


class TelegramController:
    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.store = StateStore(settings.automation_state_file)
        self.channel = ChannelPublisher(settings.telegram_monitoring_channel_id, logger)
        self._scheduler_task: asyncio.Task | None = None
        self._monitoring_lock = asyncio.Lock()

    @staticmethod
    def _authorized(context: ContextTypes.DEFAULT_TYPE) -> bool:
        return context.user_data.get("authorized") is True

    def _now(self) -> datetime:
        return datetime.now(timezone(self.settings.timezone_name))

    def _client(self):
        return make_google_client(self.settings, self.logger)

    async def _deny(self, update: Update) -> None:
        if update.callback_query:
            await update.callback_query.answer("Сначала авторизуйтесь через /start", show_alert=True)
        elif update.effective_message:
            await update.effective_message.reply_text("Сначала авторизуйтесь. Введите секретное слово:")

    async def _show_main(self, message, *, edit: bool = False) -> None:
        method = message.edit_message_text if edit else message.reply_text
        await method("🤖 Главное меню", reply_markup=main_menu())

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self._authorized(context):
            context.user_data.pop("state", None)
            await self._show_main(update.effective_message)
            return
        context.user_data.clear()
        context.user_data["state"] = STATE_SECRET
        await update.effective_message.reply_text("Введите секретное слово:")

    async def _edit_progress(self, progress, text: str) -> bool:
        try:
            await progress.edit_text(text)
            return True
        except Exception as exc:
            self.logger.warning("Не удалось обновить сообщение прогресса: %s", type(exc).__name__)
            return False

    async def _wait_notice(self, progress) -> None:
        await asyncio.sleep(15)
        await self._edit_progress(progress, "😹 Я не завис. Работа продолжается…")

    @staticmethod
    async def _cancel_waiter(waiter: asyncio.Task) -> None:
        if not waiter.done():
            waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiter

    async def _generate(self, update: Update, context, interval, *, end_date_only=False) -> None:
        message = update.callback_query.message if update.callback_query else update.message
        context.user_data.pop("state", None)
        progress = await message.reply_text("🔎 Формирую отчёт…")
        await self.channel.event("🔎 Начало мониторинга", split=split_telegram_text)
        waiter = asyncio.create_task(self._wait_notice(progress))
        try:
            _matching, report, _saved = await asyncio.to_thread(
                generate_report_bundle, self.settings, interval, self.logger,
                use_end_date_only=end_date_only,
            )
            await self._cancel_waiter(waiter)
            text = render_text(report)
            self.store.update(
                last_monitoring_at=self._now().isoformat(timespec="seconds"),
                last_monitoring_result="Успешно",
            )
            for chunk in split_telegram_text(text):
                await message.reply_text(chunk)
            await self.channel.publish(text, split=split_telegram_text)
            await self.channel.event("✅ Мониторинг успешно завершён", split=split_telegram_text)
            await self._edit_progress(progress, "✅ Отчёт готов.")
            await message.reply_text("Выберите дальнейшее действие:", reply_markup=home_keyboard())
        except Exception as exc:
            await self._cancel_waiter(waiter)
            self.store.update(
                last_monitoring_at=self._now().isoformat(timespec="seconds"),
                last_monitoring_result=f"Ошибка: {type(exc).__name__}",
            )
            self.logger.exception("Ошибка формирования Telegram-отчёта: %s", type(exc).__name__)
            await self.channel.event(f"❌ Ошибка мониторинга: {type(exc).__name__}", split=split_telegram_text)
            await self._edit_progress(progress, "❌ Не удалось сформировать отчёт.")
            await message.reply_text("Вернуться в меню:", reply_markup=home_keyboard())
        finally:
            await self._cancel_waiter(waiter)

    async def _read_email_settings(self):
        client = self._client()
        recipients, times = await asyncio.gather(
            asyncio.to_thread(client.read_recipients),
            asyncio.to_thread(client.read_schedule_times),
        )
        return recipients, times

    async def _email_settings_text(self, loaded=None) -> tuple[str, list[str], list[str]]:
        recipients, times = loaded or await self._read_email_settings()
        state = self.store.get()
        next_run = next_scheduled_run(times, self._now()) if state.enabled else None
        text = (
            "✉️ Настроить письма\n\n"
            f"Автоматическая рассылка {'включена' if state.enabled else 'выключена'}\n"
            f"Времена: {', '.join(times) if times else 'не настроены'}\n"
            f"Ближайшая отправка: {next_run.strftime('%d.%m.%Y %H:%M') if next_run else 'не запланирована'}\n"
            f"Последняя попытка: {format_user_datetime(state.last_email_at, self.settings.timezone_name)}\n"
            f"Результат: {state.last_email_result or 'Ещё не выполнялось'}"
        )
        return text, recipients, times

    def _email_keyboard(self) -> InlineKeyboardMarkup:
        enabled = self.store.get().enabled
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Получатели", callback_data="recipients_list")],
            [InlineKeyboardButton("➕ Добавить получателя", callback_data="recipient_add")],
            [InlineKeyboardButton("🕒 Расписание", callback_data="schedule")],
            [InlineKeyboardButton(
                "⏸ Выключить рассылку" if enabled else "▶️ Включить рассылку",
                callback_data="toggle_automation",
            )],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="main")],
        ])

    def _recipient_keyboard(self, recipients, selected) -> InlineKeyboardMarkup:
        """Совместимый рендер списка; отправка писем из ручного сценария удалена."""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(("✅ " if i in selected else "☐ ") + email, callback_data=f"recipient:{i}")]
            for i, email in enumerate(recipients)
        ])

    async def _show_email_settings(self, message, loaded=None) -> None:
        try:
            text, _recipients, _times = await self._email_settings_text(loaded)
            await message.reply_text(text, reply_markup=self._email_keyboard())
        except Exception as exc:
            self.logger.exception("Ошибка чтения настроек писем: %s", type(exc).__name__)
            await message.reply_text("Не удалось получить настройки писем.", reply_markup=home_keyboard())

    async def _show_recipients(self, message, context) -> None:
        try:
            recipients = await asyncio.to_thread(self._client().read_recipients)
            context.user_data["recipients"] = recipients
            text = "📋 Получатели\n\n" + ("\n".join(recipients) if recipients else "Список пуст")
            rows = [[InlineKeyboardButton("🗑 " + value, callback_data=f"recipient_delete:{i}")]
                    for i, value in enumerate(recipients)]
            rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="email_settings")])
            await message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))
        except Exception as exc:
            self.logger.exception("Ошибка чтения получателей: %s", type(exc).__name__)
            await message.reply_text("Не удалось получить список получателей.", reply_markup=home_keyboard())

    async def _show_schedule(self, message, context) -> None:
        try:
            times = await asyncio.to_thread(self._client().read_schedule_times)
            context.user_data["schedule_times"] = times
            rows = [[InlineKeyboardButton("🗑 " + value, callback_data=f"schedule_delete:{i}")]
                    for i, value in enumerate(times)]
            rows.extend([
                [InlineKeyboardButton("➕ Добавить время", callback_data="schedule_add")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="email_settings")],
            ])
            await message.reply_text(
                "🕒 Расписание\n\n" + (", ".join(times) if times else "Время не настроено"),
                reply_markup=InlineKeyboardMarkup(rows),
            )
        except Exception as exc:
            self.logger.exception("Ошибка чтения расписания: %s", type(exc).__name__)
            await message.reply_text("Не удалось получить расписание.", reply_markup=home_keyboard())

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(context):
            await self._deny(update)
            return
        await self._show_status(update.effective_message)

    async def _show_status(self, message) -> None:
        state = self.store.get()
        try:
            recipients, times = await self._read_email_settings()
            next_run = next_scheduled_run(times, self._now()) if state.enabled else None
            app_status = "Работает"
        except Exception as exc:
            self.logger.error("Ошибка обновления статуса: %s", type(exc).__name__)
            recipients, times, next_run = [], [], None
            app_status = "Ошибка получения настроек"
        text = (
            "📊 Состояние приложения\n\n"
            f"Приложение: {app_status}\n"
            f"Автоматическая рассылка: {'включена' if state.enabled else 'выключена'}\n"
            f"Времена: {', '.join(times) if times else 'не настроены'}\n"
            f"Ближайшая отправка: {next_run.strftime('%d.%m.%Y %H:%M') if next_run else 'не запланирована'}\n"
            f"Получателей: {len(recipients)}\n"
            f"Последний мониторинг: {format_user_datetime(state.last_monitoring_at, self.settings.timezone_name)}\n"
            f"Результат мониторинга: {state.last_monitoring_result or 'Ещё не выполнялось'}\n"
            f"Последняя попытка письма: {format_user_datetime(state.last_email_at, self.settings.timezone_name)}\n"
            f"Результат письма: {state.last_email_result or 'Ещё не выполнялось'}\n"
            f"Служебный канал: {'настроен' if self.channel.configured else 'не настроен'}"
        )
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить статус", callback_data="status")],
            [InlineKeyboardButton("📡 Проверить канал", callback_data="channel_test")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="main")],
        ]))

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(context):
            await self._deny(update)
            return
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if data in {"main", "menu"}:
            context.user_data.clear()
            context.user_data["authorized"] = True
            await self._show_main(query, edit=True)
        elif data == "monitoring":
            await query.edit_message_text("🔎 Сделать мониторинг", reply_markup=monitoring_menu())
        elif data == "last12":
            await self._generate(update, context, lookback_interval(12, timezone_name=self.settings.timezone_name), end_date_only=True)
        elif data == "interval":
            context.user_data["state"] = STATE_INTERVAL
            await query.message.reply_text(interval_help(), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="monitoring")]]))
        elif data == "email_settings":
            context.user_data.pop("state", None)
            await self._show_email_settings(query.message)
        elif data == "recipients_list":
            await self._show_recipients(query.message, context)
        elif data == "recipient_add":
            context.user_data["state"] = STATE_ADD_RECIPIENT
            await query.message.reply_text("Введите email:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="email_settings")]]))
        elif data.startswith("recipient_delete:"):
            values = context.user_data.get("recipients", [])
            try:
                value = values[int(data.split(":", 1)[1])]
            except (ValueError, IndexError):
                await query.message.reply_text("Список получателей изменился. Обновите его.")
                return
            context.user_data["delete_recipient"] = value
            await query.message.reply_text(f"Удалить {value}?", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Удалить", callback_data="recipient_delete_confirm"),
                InlineKeyboardButton("⬅️ Назад", callback_data="recipients_list"),
            ]]))
        elif data == "recipient_delete_confirm":
            value = context.user_data.pop("delete_recipient", None)
            if not value:
                await query.message.reply_text("Получатель не выбран.")
                return
            try:
                status = await asyncio.to_thread(self._client().delete_recipient, value)
            except Exception as exc:
                self.logger.exception("Ошибка удаления получателя: %s", type(exc).__name__)
                await query.message.reply_text("Не удалось удалить получателя. Попробуйте позже.")
                return
            await query.message.reply_text("Получатель удалён." if status == "deleted" else "Получатель уже отсутствует.")
            await self._show_recipients(query.message, context)
        elif data == "schedule":
            await self._show_schedule(query.message, context)
        elif data == "schedule_add":
            context.user_data["state"] = STATE_ADD_TIME
            await query.message.reply_text("Введите время в формате HH:MM:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="schedule")]]))
        elif data.startswith("schedule_delete:"):
            values = context.user_data.get("schedule_times", [])
            try:
                value = values[int(data.split(":", 1)[1])]
            except (ValueError, IndexError):
                await query.message.reply_text("Расписание изменилось. Обновите его.")
                return
            try:
                status = await asyncio.to_thread(self._client().delete_schedule_time, value)
            except Exception as exc:
                self.logger.exception("Ошибка удаления времени: %s", type(exc).__name__)
                await query.message.reply_text("Не удалось удалить время. Попробуйте позже.")
                return
            await query.message.reply_text("Время удалено." if status == "deleted" else "Время уже отсутствует.")
            await self._show_schedule(query.message, context)
        elif data == "toggle_automation":
            current = self.store.get().enabled
            loaded = None
            if not current:
                try:
                    loaded = await self._read_email_settings()
                except Exception as exc:
                    self.logger.exception("Ошибка проверки настроек рассылки: %s", type(exc).__name__)
                    await query.message.reply_text(
                        "Рассылка не включена: не удалось проверить получателей и расписание."
                    )
                    return
                recipients, times = loaded
                problem = automation_enable_error(recipients, times)
                if problem:
                    await query.message.reply_text(
                        "Рассылка не включена: " + problem + "."
                    )
                    return
            try:
                saved = self.store.update(enabled=not current)
            except OSError as exc:
                self.logger.error("Не удалось сохранить состояние рассылки: %s", type(exc).__name__)
                await query.message.reply_text("Не удалось сохранить состояние рассылки.")
                return
            if saved.enabled == current:
                await query.message.reply_text("Не удалось подтвердить сохранение состояния рассылки.")
                return
            await query.message.reply_text("Рассылка включена." if saved.enabled else "Рассылка выключена.")
            await self._show_email_settings(query.message, loaded=loaded)
        elif data == "status":
            await self._show_status(query.message)
        elif data == "channel_test":
            if not self.channel.configured:
                await query.message.reply_text("Служебный канал не настроен.")
            else:
                ok = await self.channel.event("📡 Проверка служебного канала: связь установлена", split=split_telegram_text)
                await query.message.reply_text("Канал доступен." if ok else "Не удалось опубликовать сообщение в канал.")

    async def text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        state = context.user_data.get("state")
        if not self._authorized(context):
            if state != STATE_SECRET:
                context.user_data.clear()
                context.user_data["state"] = STATE_SECRET
                await self._deny(update)
                return
            if verify_secret_word(update.message.text, self.settings.telegram_secret_word):
                context.user_data["authorized"] = True
                context.user_data.pop("state", None)
                await update.message.reply_text("✅ Доступ разрешён.")
                await self._show_main(update.message)
            else:
                await update.message.reply_text("Неверное секретное слово. Попробуйте ещё раз:")
            return
        if state == STATE_INTERVAL:
            try:
                interval = parse_exact_interval(update.message.text, self.settings.timezone_name)
            except ValueError as exc:
                await update.message.reply_text(f"Некорректный интервал: {exc}\n\n{interval_help()}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="monitoring")]]))
                return
            await self._generate(update, context, interval)
        elif state == STATE_ADD_RECIPIENT:
            try:
                email = normalize_email(update.message.text)
                status = await asyncio.to_thread(self._client().add_recipient, email)
            except (RecipientError, ValueError) as exc:
                await update.message.reply_text(str(exc) + "\nВведите email ещё раз:")
                return
            except Exception as exc:
                self.logger.exception("Ошибка добавления получателя: %s", type(exc).__name__)
                await update.message.reply_text("Не удалось добавить получателя. Попробуйте позже.")
                return
            context.user_data.pop("state", None)
            await update.message.reply_text("Получатель добавлен." if status == "added" else "Такой получатель уже существует.")
            await self._show_recipients(update.message, context)
        elif state == STATE_ADD_TIME:
            try:
                value = normalize_schedule_time(update.message.text)
                status = await asyncio.to_thread(self._client().add_schedule_time, value)
            except ValueError as exc:
                await update.message.reply_text(str(exc) + "\nВведите время ещё раз:")
                return
            except Exception as exc:
                self.logger.exception("Ошибка добавления времени: %s", type(exc).__name__)
                await update.message.reply_text("Не удалось добавить время. Попробуйте позже.")
                return
            context.user_data.pop("state", None)
            await update.message.reply_text("Время добавлено." if status == "added" else "Такое время уже существует.")
            await self._show_schedule(update.message, context)
        else:
            await update.message.reply_text("Используйте главное меню.", reply_markup=main_menu())

    async def run_automatic(self) -> None:
        if self._monitoring_lock.locked():
            self.logger.warning("Автоматический мониторинг пропущен: предыдущий ещё выполняется")
            return
        async with self._monitoring_lock:
            now = self._now()
            await self.channel.event("🔎 Начало автоматического мониторинга", split=split_telegram_text)
            try:
                interval = lookback_interval(12, now=now, timezone_name=self.settings.timezone_name)
                _matching, report, _saved = await asyncio.to_thread(
                    generate_report_bundle, self.settings, interval, self.logger, use_end_date_only=True
                )
                text = render_text(report)
                self.store.update(last_monitoring_at=now.isoformat(timespec="seconds"), last_monitoring_result="Успешно")
                await self.channel.publish(text, split=split_telegram_text)
                await self.channel.event("✅ Автоматический мониторинг успешно завершён", split=split_telegram_text)
            except Exception as exc:
                self.store.update(last_monitoring_at=now.isoformat(timespec="seconds"), last_monitoring_result=f"Ошибка: {type(exc).__name__}")
                self.logger.exception("Ошибка автоматического мониторинга: %s", type(exc).__name__)
                await self.channel.event(f"❌ Ошибка автоматического мониторинга: {type(exc).__name__}", split=split_telegram_text)
                return
            try:
                recipients = await asyncio.to_thread(self._client().read_recipients)
                if not recipients:
                    result = "Не отправлено: список получателей пуст"
                else:
                    await asyncio.to_thread(send_email_report, self.settings, report_text=text, recipients=recipients, report_date=report.report_date)
                    result = f"Успешно, получателей: {len(recipients)}"
                self.store.update(last_email_at=self._now().isoformat(timespec="seconds"), last_email_result=result)
                await self.channel.event(f"✉️ Автоматическая рассылка: {result}", split=split_telegram_text)
            except Exception as exc:
                result = f"Ошибка: {type(exc).__name__}"
                self.store.update(last_email_at=self._now().isoformat(timespec="seconds"), last_email_result=result)
                self.logger.exception("Ошибка автоматической email-рассылки: %s", type(exc).__name__)
                await self.channel.event(f"❌ Ошибка почтовой отправки: {type(exc).__name__}", split=split_telegram_text)

    async def scheduler_tick(self, now: datetime | None = None) -> bool:
        state = self.store.get()
        if not state.enabled:
            return False
        moment = now or self._now()
        times = await asyncio.to_thread(self._client().read_schedule_times)
        current = moment.strftime("%H:%M")
        if current not in times:
            return False
        slot = moment.strftime("%Y-%m-%dT%H:%M")
        if state.last_automatic_slot == slot:
            return False
        self.store.update(last_automatic_slot=slot)
        await self.run_automatic()
        return True

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                await self.scheduler_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.error("Ошибка цикла планировщика: %s", type(exc).__name__)
            await asyncio.sleep(15)

    async def on_startup(self, application: Application) -> None:
        self.channel.bot = application.bot
        await self.channel.event("🟢 Monitoring Bot V3 запущен", split=split_telegram_text)
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def on_shutdown(self, application: Application) -> None:
        if self._scheduler_task:
            self._scheduler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._scheduler_task


def build_application(settings: Settings, logger: logging.Logger) -> Application:
    settings.validate_telegram()
    controller = TelegramController(settings, logger)
    application = Application.builder().token(settings.telegram_bot_token).post_init(controller.on_startup).post_shutdown(controller.on_shutdown).build()
    application.add_handler(CommandHandler("start", controller.start))
    application.add_handler(CommandHandler("status", controller.status))
    application.add_handler(CallbackQueryHandler(controller.callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, controller.text))
    return application


def run_telegram_bot(settings: Settings, logger: logging.Logger) -> None:
    application = build_application(settings, logger)
    logger.info("Telegram-бот V3 запущен")
    application.run_polling(allowed_updates=["message", "callback_query"])
