"""Shared bank-fact schema and lossless normalization helpers."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Literal, TypedDict


class Transaction(TypedDict):
    transaction_id: str
    bank: Literal['KOTAK', 'SBI']
    account: str | None
    transaction_date: str
    amount: str
    direction: Literal['debit', 'credit']
    raw_description: str
    reference: str | None
    balance_after: str | None


FIELDS = tuple(Transaction.__annotations__)


def money(value: str, *, signed: bool = False) -> str:
    """Normalize decimal amounts; reject malformed grouping and sub-cent values."""
    text = str(value).strip()
    pattern = r'[+-]?(?:\d+|\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.\d+)?'
    if not re.fullmatch(pattern, text):
        raise ValueError('invalid monetary value')
    try:
        number = Decimal(text.replace(',', ''))
        cents = number.quantize(Decimal('0.01'))
    except InvalidOperation as exc:
        raise ValueError('invalid monetary value') from exc
    if number != cents or (not signed and number < 0):
        raise ValueError('negative amount or unsupported sub-cent precision')
    return format(cents if cents else Decimal('0.00'), '.2f')


def kotak_date(value: str) -> str:
    return datetime.strptime(value.strip(), '%d-%m-%Y %H:%M:%S').date().isoformat()


def optional_text(value: str) -> str | None:
    return value if value.strip() else None
