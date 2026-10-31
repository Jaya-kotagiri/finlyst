"""Independent amount partitions: no bank flow or repayment is counted twice."""

import unittest

from src.financial_summary import transaction_summary, obligation_summary


def row(identifier, amount, direction, treatment, income, expense):
    return dict(transaction_id=identifier, amount=amount, direction=direction,
                effective=dict(treatment=treatment, income=income, expense=expense))


class FinancialSummaryTests(unittest.TestCase):
    def test_asset_moves_and_reimbursements_are_not_income_or_spending(self):
        rows = [row('salary','1000.00','credit','salary_income',True,False),
                row('purchase','100.00','debit','expense',False,True),
                row('out','500.00','debit','self_transfer',False,False),
                row('in','500.00','credit','self_transfer',False,False),
                row('cash','200.00','debit','cash_conversion',False,False),
                row('reimbursement','80.00','credit','reimbursement_received',False,False),
                row('advance','100.00','debit','loan_advance',False,False),
                row('unknown','7.00','debit','unknown',None,None),
                row('shared','90.00','debit','recoverable_or_shared_expense',False,None),
                row('partial','40.00','credit','unknown',None,None)]
        effects = {'partial':dict(verified_principal_repayment_amount='30.00',
            remaining_transaction_amount_unclassified_by_this_stage='10.00', repayment_is_income_or_expense=False,
            excess_treatment_requires_review=False)}
        result = transaction_summary(rows,effects)
        self.assertEqual((result['recognized_income'],result['recognized_expense']),('1000.00','100.00'))
        self.assertEqual(result['bank_movements'],dict(debit='997.00',credit='1620.00',net='623.00'))
        self.assertEqual(result['excluded_non_income_expense']['debit'],'800.00')
        self.assertEqual(result['excluded_non_income_expense']['credit'],'610.00')
        self.assertEqual(result['uncertain'],dict(debit='97.00',credit='10.00',transaction_count=3))
        self.assertEqual(result['verified_principal_repayments_included_in_excluded'],'30.00')
        self.assertFalse(result['income_expense_complete'])

    def test_unexplained_overpayment_remains_uncertain(self):
        effect = dict(verified_principal_repayment_amount='100.00',remaining_transaction_amount_unclassified_by_this_stage='5.00',
                      repayment_is_income_or_expense=None,excess_treatment_requires_review=True)
        result = transaction_summary([row('r','105.00','credit','reimbursement_received',False,False)],{'r':effect})
        self.assertEqual(result['uncertain']['credit'],'105.00')
        self.assertEqual(result['recognized_income'],'0.00')
        self.assertFalse(result['income_expense_complete'])

    def test_duplicate_ids_and_invalid_overlay_are_rejected(self):
        item=row('r','0.30','debit','expense',False,True)
        with self.assertRaises(ValueError): transaction_summary([item,item],{})
        with self.assertRaises(ValueError):
            transaction_summary([item],{'r':dict(verified_principal_repayment_amount='0.31',
                remaining_transaction_amount_unclassified_by_this_stage='0.00',repayment_is_income_or_expense=False,
                excess_treatment_requires_review=False)})

    def test_decimal_arithmetic_and_empty_selection(self):
        result=transaction_summary([row('a','0.10','debit','expense',False,True),row('b','0.20','debit','expense',False,True)],{})
        self.assertEqual(result['recognized_expense'],'0.30')
        self.assertEqual(transaction_summary([],{})['transaction_count'],0)

    def test_unknown_outstanding_is_not_zero_including_mixed_closed_claims(self):
        rows=[dict(direction='receivable',status='CLOSED',outstanding_amount='0.00',original_principal='100.00'),
              dict(direction='receivable',status='UNASSESSED',outstanding_amount=None,original_principal=None),
              dict(direction='payable',status='PARTIAL',outstanding_amount='12.34',original_principal='50.00')]
        result=obligation_summary(rows)
        self.assertIsNone(result['receivable']['total_outstanding'])
        self.assertEqual(result['receivable']['confirmed_outstanding_subtotal'],'0.00')
        self.assertEqual(result['receivable']['unassessed_count'],1)
        self.assertEqual(result['payable']['total_outstanding'],'12.34')
