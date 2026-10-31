"""Synthetic coherent financial snapshots for importer regressions."""

from copy import deepcopy
from decimal import Decimal

from src.identities import migrate
from src.reconcile import reconcile
from src.settlements import build as settle
from tests.support import doc, origin, prepared, receipt, with_semantics


def snapshot(specs=None, semantics=None, opening_balances=None):
    specs = specs or {
        'KOTAK': [origin(), receipt(), dict(amount='25.00', date='2026-08-03')],
        'SBI': [dict(amount='20.00', date='2026-08-03')],
    }
    documents = []
    for bank, rows in specs.items():
        document = doc(bank, rows)
        if semantics and bank in semantics:
            document = with_semantics(document, semantics[bank])
        transactions = []
        balance = Decimal((opening_balances or {}).get(bank, '10000.00'))
        for row in document['transactions']:
            transaction = deepcopy(row['bank_transaction'])
            balance += Decimal(transaction['amount']) * (1 if transaction['direction'] == 'credit' else -1)
            transaction.update(account='1234567890' if bank == 'KOTAK' else '9876543210',
                               balance_after=format(balance, '.2f'))
            transactions.append(transaction)
        documents.append(reconcile(transactions, document['event_source_document']))
    data = prepared(documents)
    return dict(banks={d['bank'].lower(): [r['bank_transaction'] for r in d['transactions']] for d in documents},
                events={d['bank'].lower(): d['event_source_document'] for d in documents},
                reconciled=data[0], relationships=data[1], classification=data[2], coverage=data[3],
                origins=data[4], settlements=settle(*data))


def resolved_snapshot(specs=None, semantics=None):
    data = snapshot(specs, semantics)
    return data, migrate(data)
