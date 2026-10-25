"""Shared synthetic builders and isolated workspaces; no private fixture files."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from src.classification import build as classify
from src.obligations import build as build_origins
from src.reconcile import digest, reconcile
from src.relationships import derive, flatten
from src.settlements import build as build_settlements


def temporary_directory():
    """Create a disposable workspace inside the existing tests directory."""
    return TemporaryDirectory(prefix='finlyst-test-', dir=Path(__file__).resolve().parent)


def doc(bank, specs):
    transactions, rows, events = [], [], []
    for i, spec in enumerate(specs, 1):
        t = dict(transaction_id=f'{bank}_{i:04d}', bank=bank, account='synthetic',
            transaction_date=spec.get('date', '2026-08-01'), amount=spec.get('amount', '100.00'),
            direction=spec.get('direction', 'debit'), raw_description=spec.get('narration', f'Synthetic {i}'),
            reference=None, balance_after='500.00')
        transactions.append(t)
        if 'note' not in spec:
            continue
        j = len(events) + 1
        row = dict(source_id=f'sheet:{i}', date=t['transaction_date'], amount=t['amount'],
                   direction=t['direction'], narration=t['raw_description'], note=spec['note'])
        if spec.get('follow_up'):
            row['follow_up'] = spec['follow_up']
        rows.append(row)
        events.append(dict(event_id=f'{bank}_EVENT_{j:04d}', source_id=row['source_id'],
            event_type=spec.get('event_type'), date=row['date'], amount=row['amount'], direction=row['direction'],
            currency=None, merchant=None, payee=None, payer=None, category=None, subcategory=None,
            description=None, purpose_context=None, entities=[], components=spec.get('components', []),
            matching_clues=[], confidence='high', ambiguities=[], additional_context=[]))
    return reconcile(transactions, dict(bank=bank, source_rows=rows, events=events, source_rows_sha256=digest(rows)))


def share(party='Alice', amount='40.00'):
    return dict(kind='receivable', amount=amount, party=party, status='planned', description='Share')


def with_semantics(document, fields):
    source = deepcopy(document['event_source_document'])
    for event, values in zip(source['events'], fields):
        event.update(values)
    return reconcile([r['bank_transaction'] for r in document['transactions']], source)


def inputs(documents):
    relationships = derive(documents)
    classification = classify(documents, relationships)
    audit = {'transactions': [dict(r['bank'], event_id=r['event']['event_id'] if r['event'] else None,
                                   effective_treatment=t['effective']['treatment'])
                              for r, t in zip(flatten(documents).values(), classification['transactions'])]}
    return documents, relationships, classification, audit


def prepared(documents):
    previous = inputs(documents)
    return (*previous, build_origins(*previous))


def settle(documents):
    return build_settlements(*prepared(documents))


def origin(amount='100.00', party='Alice', follow_up=None):
    return dict(amount=amount, note=f'{party} share {amount}', components=[share(party, amount)], follow_up=follow_up)


def receipt(amount='100.00', note='Alice repaid in full for KOTAK_0001', date='2026-08-02'):
    return dict(direction='credit', amount=amount, note=note, date=date)


def single(result):
    row, = result['obligation_settlement_view']
    return row
