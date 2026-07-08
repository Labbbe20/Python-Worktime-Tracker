"""Helpers for flextime amounts, tracking start dates, and balance status."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

from .models import parse_date


ZERO = Decimal("0")
MINUTES_PER_HOUR = Decimal("60")


@dataclass(frozen=True)
class BalanceStatus:
    key: str
    css_class: str
    label: str


def parse_decimal_hours_to_minutes(value: Any, field_label: str = "Stunden") -> int:
    """Parse German/English decimal hour input into rounded whole minutes."""

    if value is None:
        return 0
    raw = str(value).strip()
    if not raw:
        return 0
    normalized = raw.replace(",", ".")
    try:
        hours = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"{field_label} muss eine Zahl sein, z. B. 50,89 oder -3.75.") from exc
    if not hours.is_finite():
        raise ValueError(f"{field_label} muss eine endliche Zahl sein.")
    minutes = (hours * MINUTES_PER_HOUR).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(minutes)


def format_minutes_as_decimal_hours(minutes: int, signed: bool = True) -> str:
    """Format minutes as German decimal hours with two decimal places."""

    value = Decimal(int(minutes)) / MINUTES_PER_HOUR
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "+" if signed and quantized >= ZERO else ""
    return f"{sign}{str(quantized).replace('.', ',')} h"


def get_tracking_start_date(settings: Mapping[str, str]) -> Date | None:
    raw = str(settings.get("tracking_start_date", "") or "").strip()
    if not raw:
        return None
    return parse_date(raw)


def is_before_tracking_start(date_value: str | Date, settings: Mapping[str, str]) -> bool:
    start = get_tracking_start_date(settings)
    if not start:
        return False
    day = parse_date(date_value) if isinstance(date_value, str) else date_value
    return day < start


def get_initial_flextime_minutes(settings: Mapping[str, str]) -> int:
    raw_minutes = settings.get("initial_flextime_minutes", "0") or "0"
    try:
        return int(raw_minutes)
    except (TypeError, ValueError):
        return parse_decimal_hours_to_minutes(raw_minutes, "Anfangssaldo Gleitzeit")


def classify_balance(minutes: int) -> BalanceStatus:
    """Classify flextime according to the 50-hour company rule."""

    if minutes < 0:
        return BalanceStatus("negative", "balance-negative", "Unter 0 Stunden")
    if minutes <= 45 * 60:
        return BalanceStatus("ok", "balance-ok", "0 bis 45 Stunden")
    if minutes <= 50 * 60:
        return BalanceStatus("warning", "balance-warning", "Über 45 bis 50 Stunden")
    return BalanceStatus("danger", "balance-danger", "Über 50 Stunden")

