"""Единая точка запуска Telegram-бота и встроенного планировщика."""

from __future__ import annotations

from monitoring_bot_v3.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["run-telegram"]))
