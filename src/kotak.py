"""Convert the inspected Kotak CSV layout into shared bank-fact JSON."""

import argparse
import csv
import json
from pathlib import Path

from .models import Transaction, kotak_date, money, optional_text
from .validation import validate_transactions, write_json


HEADER = ['Sl. No.', 'Transaction Date', 'Value Date', 'Description',
          'Chq /Ref No.', 'Amount', 'Dr / Cr', 'Balance', 'Dr / Cr']
FOOTER = [
    'Important Note:',
    'The transaction date refers to the date & time when the transactions have been posted in the system.',
    'The number of transactions in the PDF statement and CSV statement may vary due to the use of different sorting methods and underlying dates.',
    'CSV statement with value date is recommended to be used for reconciliation purposes.',
    'You may call our 24-hour Customer Contact Centre at our number 1800 4100',
    'Write to us at Customer Contact Centre. Kotak Mahindra Bank Ltd. Post Box Number 16344, Mumbai 400 013',
]


def parse_kotak(path: Path) -> list[Transaction]:
    transactions = []
    account = None
    started = False
    closed = False
    with Path(path).open(encoding='utf-8-sig', newline='') as source:
        for row_number, row in enumerate(csv.reader(source), 1):
            if not any(cell.strip() for cell in row):
                continue
            if row == HEADER:
                if closed:
                    raise ValueError(f'Kotak row {row_number}: header after closing balance')
                started = True
                continue
            if not started:
                if len(row) == 6 and row[4] == 'Account No.':
                    account = optional_text(row[5])
                continue
            if row[0] == 'Closing Balance' and len(row) == 2 and not closed:
                closed = True
                continue
            if closed:
                if len(row) == 1 and row[0] in FOOTER:
                    continue
                raise ValueError(f'Kotak row {row_number}: unrecognized footer row')
            try:
                if len(row) != len(HEADER) or row[0] != str(len(transactions) + 1):
                    raise ValueError('unexpected columns or source sequence')
                direction = {'DR': 'debit', 'CR': 'credit'}[row[6]]
                balance = None
                if row[7].strip():
                    balance = money(row[7])
                    if row[8] not in ('DR', 'CR'):
                        raise ValueError('unknown balance direction')
                    if row[8] == 'DR' and balance != '0.00':
                        balance = '-' + balance
                elif row[8].strip():
                    raise ValueError('balance marker without balance')
                transactions.append(Transaction(
                    transaction_id=f'KOTAK_{len(transactions) + 1:04d}', bank='KOTAK',
                    account=account, transaction_date=kotak_date(row[1]), amount=money(row[5]),
                    direction=direction, raw_description=row[3],
                    reference=optional_text(row[4]), balance_after=balance,
                ))
            except (ValueError, KeyError) as exc:
                raise ValueError(f'Kotak row {row_number}: unsafe transaction ({exc})') from exc
    if not started or not transactions or not closed:
        raise ValueError('Kotak: missing header, transactions, or closing balance')
    validate_transactions(transactions)
    return transactions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'output/bank/kotak_transactions.json')
    args = parser.parse_args()
    print(json.dumps(write_json(parse_kotak(args.source), args.output), indent=2))


if __name__ == '__main__':
    main()
