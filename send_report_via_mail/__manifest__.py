{
    'name': 'Send Report Via Email',
    'version': '19.0.1.0.0',
    'author': 'Madfox Solutions',
    'website': 'https://madfox.solutions/',
    'category': 'Send Mail Implementation',
    'sequence': 380,
    'summary': 'Send Mail Implementation',
    'description': """Send Report Via Email""",
    'depends': ['base', 'payment', 'account', 'sale', 'account_reports'],
    'data': [
        'security/ir.model.access.csv',
        'data/report_mail.xml',
        'views/send_mail.xml',
        'reports/report_profit_and_loss_views.xml',
        'views/report_trip_profit_views.xml'
    ],
    'application': True,
    'installable': False,
    'license': 'LGPL-3',
}
