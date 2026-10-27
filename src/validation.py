"""Strict shared validation, duplicate reporting, and JSON output."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
import re

from .models import FIELDS
from .output_models import BankTransaction, BankTransactions
from .schema_validation import validate_schema


def validate_transaction(transaction: dict) -> None:
    validate_schema(BankTransaction, transaction, "bank transaction")
    if set(transaction) != set(FIELDS):
        raise ValueError('transaction fields differ from the shared schema')
    bank = transaction['bank']
    if bank not in ('KOTAK', 'SBI'):
        raise ValueError('invalid bank')
    identifier = transaction['transaction_id']
    if not isinstance(identifier, str) or not re.fullmatch(bank + r'_\d{4,}', identifier):
        raise ValueError('invalid transaction ID')
    value = transaction['transaction_date']
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('invalid ISO date')
    date.fromisoformat(value)
    if transaction['direction'] not in ('debit', 'credit'):
        raise ValueError('invalid direction')
    for field in ('amount', 'balance_after'):
        value = transaction[field]
        if field == 'balance_after' and value is None:
            continue
        pattern = r'\d+\.\d{2}' if field == 'amount' else r'-?\d+\.\d{2}'
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError(f'invalid {field}')
    if not isinstance(transaction['raw_description'], str) or not transaction['raw_description'].strip():
        raise ValueError('missing original narration')
    for field in ('account', 'reference'):
        value = transaction[field]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f'invalid {field}')


def validate_transactions(transactions: list[dict]) -> dict:
    validate_schema(BankTransactions, transactions, "bank transactions")
    seen = {}
    duplicates = []
    for index, transaction in enumerate(transactions, 1):
        validate_transaction(transaction)
        if transaction['transaction_id'] != f"{transaction['bank']}_{index:04d}":
            raise ValueError('transaction IDs do not follow source order')
        key = tuple(transaction[field] for field in FIELDS if field != 'transaction_id')
        if key in seen:
            duplicates.append([seen[key], transaction['transaction_id']])
        else:
            seen[key] = transaction['transaction_id']
    return {'validated': len(transactions), 'duplicate_pairs': duplicates}


def summary(transactions: list[dict]) -> dict:
    report = validate_transactions(transactions)
    for direction in ('debit', 'credit'):
        selected = [t for t in transactions if t['direction'] == direction]
        report[direction] = {
            'count': len(selected),
            'total': format(sum((Decimal(t['amount']) for t in selected), Decimal('0.00')), '.2f'),
        }
    return report


def write_json(transactions: list[dict], destination: Path) -> dict:
    report = summary(transactions)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(transactions, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # Report duplicates without silently removing potentially legitimate bank entries.
    return report
