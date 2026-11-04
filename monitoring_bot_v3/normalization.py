"""Детерминированная консервативная нормализация идентификаторов недвижимости."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_CADASTRAL_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2})\s*:\s*(\d{1,2})\s*:\s*(\d{6,7})\s*:\s*(\d+)(?!\d)"
)


def extract_cadastral_numbers(value: str | None) -> tuple[str, ...]:
    """Извлекает только полные номера, сохраняя порядок и не достраивая значения."""
    if not value:
        return ()
    found: list[str] = []
    seen: set[str] = set()
    for match in _CADASTRAL_PATTERN.finditer(str(value)):
        number = ":".join(match.groups())
        if number not in seen:
            seen.add(number)
            found.append(number)
    return tuple(found)


def normalize_cadastral_number(value: str | None) -> str | None:
    numbers = extract_cadastral_numbers(value)
    return numbers[0] if len(numbers) == 1 else None


def cadastral_quarter(number: str | None) -> str | None:
    normalized = normalize_cadastral_number(number)
    return normalized.rsplit(":", 1)[0] if normalized else None


_ABBREVIATIONS = (
    (r"\bг\.?\s+", "город "),
    (r"\bул\.?\s+", "улица "),
    (r"\bпр\s*[-.]?\s*т\.?\s+", "проспект "),
    (r"\bпросп\.?\s+", "проспект "),
    (r"\bд\.?\s*(?=\d)", "дом "),
    (r"\bкорп(?:\.|\b)\s*", "корпус "),
    (r"\bстр(?:\.|\b)\s*", "строение "),
    (r"\bпер\.?\s+", "переулок "),
    (r"\bнаб\.?\s+", "набережная "),
    (r"\bш\.?\s+", "шоссе "),
    (r"\bпл\.?\s+", "площадь "),
)

_LOCALITY_TYPES = "рабочий поселок|город|село|деревня|поселок"
_STREET_TYPES = "улица|проспект|переулок|набережная|шоссе|площадь"


@dataclass(frozen=True)
class NormalizedAddress:
    original: str
    key: str
    is_identifiable: bool
    reason: str | None = None


def _plain(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    for pattern, replacement in _ABBREVIATIONS:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"[\u00a0.;]", " ", text)
    text = re.sub(r"\s*[-–—]\s*", "-", text)
    return re.sub(r"\s+", " ", text).strip(" ,- ")


def _locality_name(value: str) -> str:
    value = _plain(value)
    return re.sub(rf"^(?:{_LOCALITY_TYPES})\s+", "", value).strip()


def _street_component(text: str) -> tuple[str, str] | None:
    """Возвращает (тип, название) для префиксной или суффиксной записи."""
    segments = [_plain(part) for part in text.split(",") if _plain(part)]
    for segment in segments:
        prefix = re.fullmatch(rf"({_STREET_TYPES})\s+(.+)", segment)
        if prefix:
            return prefix.group(1), prefix.group(2).strip()
        suffix = re.fullmatch(rf"(.+?)\s+({_STREET_TYPES})", segment)
        if suffix:
            return suffix.group(2), suffix.group(1).strip()

    compact = _plain(text.replace(",", " "))
    prefix = re.search(
        rf"\b({_STREET_TYPES})\s+(.+?)(?=\s+дом\b|\s+корпус\b|\s+строение\b|$)",
        compact,
    )
    if prefix:
        return prefix.group(1), prefix.group(2).strip()
    suffix = re.search(
        rf"(?:^|,|\s)(.+?)\s+({_STREET_TYPES})(?=\s+дом\b|\s+корпус\b|\s+строение\b|$)",
        compact,
    )
    if suffix:
        name = re.sub(rf"^(?:{_LOCALITY_TYPES})\s+[^ ]+\s+", "", suffix.group(1))
        return suffix.group(2), name.strip()
    return None


def normalize_address(
    value: str | None, *, locality: str | None = None
) -> NormalizedAddress | None:
    """Строит консервативный структурированный ключ без fuzzy matching."""
    if not value or not str(value).strip():
        return None
    original = str(value).strip()
    text = _plain(original)
    segments = [_plain(part) for part in original.split(",") if _plain(part)]

    locality_value = _locality_name(locality) if locality and locality.strip() else ""
    if not locality_value:
        explicit = re.search(
            rf"\b(?:{_LOCALITY_TYPES})\s+(.+?)"
            rf"(?=,|\s+(?:{_STREET_TYPES})\b|\s+дом\b|$)",
            text,
        )
        if explicit:
            locality_value = explicit.group(1).strip()
        elif len(segments) >= 2 and not re.search(
            rf"\b(?:{_STREET_TYPES}|дом|корпус|строение)\b", segments[0]
        ) and re.search(rf"\b(?:{_STREET_TYPES})\b", segments[1]):
            # Самостоятельный населённый пункт принимается только как отдельный
            # первый компонент перед полноценным компонентом улицы.
            locality_value = segments[0]

    street = _street_component(original)
    house = re.search(r"\bдом\s*(\d+[а-я0-9/-]*)\b", text)
    corpus = re.search(r"\bкорпус\s*(\d+[а-я0-9/-]*)\b", text)
    structure = re.search(r"\bстроение\s*(\d+[а-я0-9/-]*)\b", text)
    identifiable = bool(locality_value and street and house)
    reason = None if identifiable else "нужны населённый пункт, улица и номер дома"
    parts = [
        f"locality={locality_value}",
        f"street={street[0]} {street[1]}" if street else "street=",
        f"house={house.group(1) if house else ''}",
        f"corpus={corpus.group(1) if corpus else ''}",
        f"structure={structure.group(1) if structure else ''}",
    ]
    return NormalizedAddress(original, "|".join(parts), identifiable, reason)
