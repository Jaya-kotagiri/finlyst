"""Shared synthetic builders and isolated workspaces; no private fixture files."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from src.classification import build as classify
from src.audit import coverage_counts, reason
from src.obligations import build as build_origins
from src.reconcile import digest, reconcile
from src.relationships import derive, flatten
from src.settlements import build as build_settlements


def temporary_directory():
    """Create a disposable workspace inside the existing tests directory."""
    return TemporaryDirectory(prefix='finlyst-test-', dir=Path(__file__).resolve().parent)


def event_document(bank, rows, events):
    """A complete synthetic Stage 2 envelope, including its version/provenance."""
    return dict(schema_version='1.0', bank=bank, source_file='synthetic.xlsx', model='mock',
        source_rows=rows, events=events, source_rows_sha256=digest(rows),
        report=dict(events=len(events), batches=1 if rows else 0, validated=True,
            duplicate_events=0, ambiguous_events=sum(bool(e['ambiguities']) for e in events),
            low_confidence_events=sum(e['confidence'] == 'low' for e in events),
            rows_without_notes=sum('note' not in r for r in rows), input_tokens=0, output_tokens=0))


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
    return reconcile(transactions, event_document(bank, rows, events))


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
    rows = []
    for i, (r, t) in enumerate(zip(flatten(documents).values(), classification['transactions']), 1):
        source, event = r['source'], r['event']
        row = dict(r['bank'], bank_source_file='synthetic.csv', bank_source_location={'csv_record': i},
            notes_file='synthetic.xlsx', workbook_candidates=[source['source_id']] if source else [],
            workbook_row=dict(sheet='sheet', row=i, meaning_cell=f'C{i}', meaning=source.get('note'),
                follow_up=source.get('follow_up'), hidden=False, date=r['bank']['transaction_date']) if source else None,
            meaning_populated=bool(source.get('note')), follow_up_populated=bool(source.get('follow_up')),
            extracted_in_stage_2=bool(source), event_id=event['event_id'] if event else None,
            matched_in_3A=event is not None, semantic_meaning_preserved=True,
            effective_treatment=t['effective']['treatment'], classification_rule=t['effective']['rule'],
            original_unknown=t['effective']['treatment'] == 'unknown', semantic=t['semantic'],
            relationships=t['relationships'], review_reasons=t['review_reasons'])
        row['reason_category'], row['responsible_stage'], row['explanation'] = reason(row)
        rows.append(row)
    audit = dict(schema_version='3C.1A.1', transactions=rows, summary=coverage_counts(rows), sources={},
        verification=dict(source_sha256={}, sources_unchanged=True, classification_rebuild_identical=True,
                          deterministic_fixes=0, api_calls=0))
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
