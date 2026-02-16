{
    'name': 'Cheque Management',
    'version': '17.0.1.0.1',
    'summary': 'Cheque printing and management with wizard',
    'description': """
        Manage cheques, print them multiple times,
        track cheque numbers, print dates, and reasons.
        Includes wizard for controlled printing.
    """,
    'author': 'Rahaf Moualla',
    'website': 'http://primeshift.ae/',
    'category': 'Accounting',
    'license': 'LGPL-3',

    'depends': [
        'base',
        'account_accountant',
        'account_check_printing',
    ],

    'data': [
        'security/ir.model.access.csv',
        'wizard/cheque_wizard_view.xml',
        'views/account_cheque_menu.xml',
        'views/account_cheque_views.xml',
        'views/account_payment_view.xml',
        'reports/cheque_report.xml',
        'reports/voucher_report.xml',
        'reports/report_cheque_preview.xml',
    ],

    'application': False,
    'installable': True,
}
