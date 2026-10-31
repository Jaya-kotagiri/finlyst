"""Exact aggregation of existing classifications; no inference or database access."""

from collections import Counter
from decimal import Decimal


ZERO = Decimal('0.00')


def money(value):
    return format(value, '.2f')


def transaction_summary(transactions, effects):
    """Partition each bank movement once, applying only its verified repayment share."""
    totals = {group: {'debit': ZERO, 'credit': ZERO} for group in ('bank', 'recognized', 'excluded', 'uncertain')}
    counts = Counter()
    treatments = Counter()
    seen = set()
    principal = ZERO
    for row in transactions:
        identifier = row['transaction_id']
        if identifier in seen:
            raise ValueError('Cannot summarize duplicate bank transactions')
        seen.add(identifier)
        amount, direction = Decimal(row['amount']), row['direction']
        effective = row['effective']
        treatments[effective['treatment']] += 1
        totals['bank'][direction] += amount
        remaining = amount
        effect = effects.get(identifier)
        if effect:
            repaid = Decimal(effect['verified_principal_repayment_amount'])
            if repaid < ZERO or repaid > amount or repaid + Decimal(effect['remaining_transaction_amount_unclassified_by_this_stage']) != amount:
                raise ValueError('Repayment overlay exceeds or disagrees with the bank movement')
            if effect['repayment_is_income_or_expense'] is False and not effect['excess_treatment_requires_review']:
                principal += repaid
                totals['excluded'][direction] += repaid
                counts['excluded_transactions'] += 1
                remaining -= repaid
            else:
                totals['uncertain'][direction] += remaining
                counts['uncertain_transactions'] += 1
                continue
        if remaining == ZERO:
            continue
        flags = effective['income'], effective['expense']
        if flags == (True, False) and direction == 'credit' or flags == (False, True) and direction == 'debit':
            group = 'recognized'
        elif flags == (False, False):
            group = 'excluded'
        else:
            group = 'uncertain'
        totals[group][direction] += remaining
        if group != 'excluded' or not effect:
            counts[group + '_transactions'] += 1
    for direction in ('debit', 'credit'):
        if totals['bank'][direction] != sum((totals[group][direction] for group in ('recognized', 'excluded', 'uncertain')), ZERO):
            raise ValueError('Summary partition does not equal original bank movements')
    return dict(transaction_count=len(seen), currency='INR', by_treatment=dict(sorted(treatments.items())),
        bank_movements={**{key: money(value) for key, value in totals['bank'].items()},
                        'net': money(totals['bank']['credit'] - totals['bank']['debit'])},
        recognized_income=money(totals['recognized']['credit']), recognized_expense=money(totals['recognized']['debit']),
        recognized_net=money(totals['recognized']['credit'] - totals['recognized']['debit']),
        excluded_non_income_expense={**{key: money(value) for key, value in totals['excluded'].items()},
                                    'transaction_count': counts['excluded_transactions']},
        uncertain={**{key: money(value) for key, value in totals['uncertain'].items()},
                   'transaction_count': counts['uncertain_transactions']},
        verified_principal_repayments_included_in_excluded=money(principal),
        income_expense_complete=counts['uncertain_transactions'] == 0)


def obligation_summary(settlements):
    """Known subtotals are separate from nullable total outstanding, never guessed."""
    result = {}
    for direction in ('receivable', 'payable'):
        rows = [row for row in settlements if row['direction'] == direction]
        known = sum((Decimal(row['outstanding_amount']) for row in rows if row['outstanding_amount'] is not None), ZERO)
        unknown = sum(row['outstanding_amount'] is None for row in rows)
        result[direction] = dict(obligation_count=len(rows), by_status=dict(sorted(Counter(row['status'] for row in rows).items())),
            confirmed_outstanding_subtotal=money(known), total_outstanding=None if unknown else money(known),
            unassessed_count=unknown, unknown_principal_count=sum(row['original_principal'] is None for row in rows))
    return result
