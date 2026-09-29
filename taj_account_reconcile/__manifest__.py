{
    'name': 'TAJ Account Reconciliation',
    'version': '19.0.1.1.2',
    'summary': 'Edit the company-currency equivalent of a bank transaction safely',
    'description': """
        Makes the native exact-invoice allocation of the Odoo bank reconciliation
        widget easier to discover and adds a guarded modal wizard that edits the
        company-currency equivalent of an unreconciled foreign-currency bank
        transaction.

        The wizard only writes the standard account.bank.statement.line currency
        fields, lets Odoo synchronize the related journal entry and never creates
        or changes a res.currency.rate record.
    """,
    'category': 'Accounting',
    'author': 'TAJ Trucking',
    'license': 'LGPL-3',
    'depends': [
        'account_accountant',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/bank_statement_currency_amount_views.xml',
        'views/bank_reconciliation_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'taj_account_reconcile/static/src/js/bank_reconciliation.js',
            'taj_account_reconcile/static/src/xml/bank_reconciliation.xml',
            'taj_account_reconcile/static/src/scss/bank_reconciliation.scss',
        ],
        'web.assets_tests': [
            'taj_account_reconcile/static/tests/tours/**/*',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
