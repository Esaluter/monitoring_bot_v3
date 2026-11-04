"""Неблокирующая для бизнес-сценария публикация в служебный Telegram-канал."""

from __future__ import annotations

import logging


class ChannelPublisher:
    def __init__(self, channel_id: str, logger: logging.Logger) -> None:
        self.channel_id = str(channel_id or "").strip()
        self.logger = logger
        self.bot = None

    @property
    def configured(self) -> bool:
        return bool(self.channel_id)

    async def publish(self, text: str, *, split) -> bool:
        if not self.configured or self.bot is None:
            return False
        try:
            for chunk in split(text):
                await self.bot.send_message(chat_id=self.channel_id, text=chunk)
            return True
        except Exception as exc:
            self.logger.error(
                "Ошибка публикации в служебный канал: %s", type(exc).__name__
            )
            return False

    async def event(self, text: str, *, split) -> bool:
        return await self.publish(text, split=split)
