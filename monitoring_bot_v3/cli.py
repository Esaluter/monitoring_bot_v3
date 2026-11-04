"""Локальная read-only диагностика источников и сопоставления."""

from __future__ import annotations

import argparse
import sys

from .config import Settings
from .logging_config import configure_logging
from .matching_service import run_matching
from .models import PledgeStatus
from .report_service import generate_report_bundle
from .source_service import check_collateral_source, check_google_source, make_google_client
from .time_windows import format_interval, lookback_interval, parse_exact_interval


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitoring Bot V3 — проверка источников")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in (
        "check-sources", "check-google", "check-matching", "generate-report"
    ):
        command = subparsers.add_parser(name)
        command.add_argument(
            "--interval",
            help="Точный интервал одной строкой; по умолчанию последние N часов",
        )
        command.add_argument("--lookback-hours", type=int)

    subparsers.add_parser("check-excel")
    subparsers.add_parser("list-recipients")
    subparsers.add_parser("run-telegram")
    return parser


def _interval(args: argparse.Namespace, settings: Settings):
    if args.interval and args.lookback_hours is not None:
        raise ValueError("Укажите --interval или --lookback-hours, но не оба")
    if args.interval:
        return parse_exact_interval(args.interval, settings.timezone_name)
    return lookback_interval(
        args.lookback_hours or settings.lookback_hours,
        timezone_name=settings.timezone_name,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings.from_env()
        logger = configure_logging(
            settings.log_file,
            secrets=(
                settings.google_apps_script_api_key,
                settings.telegram_bot_token,
                settings.telegram_secret_word,
                settings.email_password,
            ),
        )
        if args.command == "run-telegram":
            from .telegram_bot import run_telegram_bot

            run_telegram_bot(settings, logger)
            return 0
        if args.command == "generate-report":
            interval = _interval(args, settings)
            print("Интервал:", format_interval(interval))
            _result, report, saved = generate_report_bundle(settings, interval, logger)
            print("HTML:", saved.html_path)
            print("JSON:", saved.json_path)
            print("TXT:", saved.txt_path)
            print(
                "Строки разделов:",
                ", ".join(str(len(section.rows)) for section in report.sections),
            )
            return 0
        if args.command == "check-matching":
            interval = _interval(args, settings)
            print("Интервал:", format_interval(interval))
            result = run_matching(settings, interval, logger)
            d = result.diagnostics
            print("Обработано объектов новостей:", d.processed_news_objects)
            print(
                "Строк реестра: физических=%s индексировано=%s пустых=%s"
                % (d.registry_rows, d.indexed_registry_rows, d.empty_registry_rows)
            )
            print("1. Точных залоговых объектов:", d.exact_collateral_count)
            print("2. Основное в инфополе:", len(result.information_field))
            print("3. Залогов в том же квартале:", d.quarter_collateral_count)
            print("Конфликтов идентификаторов:", d.conflict_count)
            print(
                "Совпадений адреса с различающимися кадастровыми номерами:",
                d.address_cadastral_mismatch_count,
            )
            print("Без данных для идентификации:", d.no_identification_count)
            print(
                "Время, сек: чтение=%.3f индексы=%.3f сопоставление=%.3f"
                % (d.read_seconds, d.index_seconds, d.matching_seconds)
            )
            for warning in d.warnings:
                print("Предупреждение:", warning)
            for item in result.exact_matches[:5]:
                print(
                    "Пример точного совпадения:",
                    item.collateral.object_id or item.collateral_key,
                    ", ".join(map(str, item.reasons)),
                    PledgeStatus.PLEDGED,
                )
            for item in result.quarter_matches[:5]:
                print(
                    "Пример квартального совпадения:",
                    item.collateral.object_id or item.collateral_key,
                    ", ".join(item.quarters),
                )
            return 0
        if args.command in {"check-sources", "check-google"}:
            interval = _interval(args, settings)
            print("Интервал:", format_interval(interval))
            google_rows = check_google_source(settings, interval, logger)
            print("Строк Google Sheets:", len(google_rows))
            if args.command == "check-google":
                return 0

        if args.command in {"check-sources", "check-excel"}:
            result = check_collateral_source(settings, logger)
            print("Строк залогового реестра:", result.total_rows)
            print("Время чтения Excel, сек:", round(result.elapsed_seconds, 3))
            return 0

        if args.command == "list-recipients":
            recipients = make_google_client(settings, logger).read_recipients()
            print("Получателей:", len(recipients))
            for email in recipients:
                print(email)
            return 0
    except Exception as exc:
        try:
            logger.exception("Проверка источников завершилась ошибкой: %s", type(exc).__name__)
        except UnboundLocalError:
            pass
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    return 0

