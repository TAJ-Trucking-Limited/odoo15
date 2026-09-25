{
    'name': 'TAJ Navirec Fleet Integration',
    'version': '19.0.1.0.0',
    'summary': 'Sync Navirec vehicle positions and telematics into Odoo Fleet',
    'category': 'Human Resources/Fleet',
    'author': 'TAJ Trucking',
    'license': 'LGPL-3',
    'depends': ['fleet'],
    'data': [
        'data/ir_cron_data.xml',
        'views/fleet_vehicle_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'external_dependencies': {'python': ['requests']},
    'installable': True,
    'application': False,
    'auto_install': False,
}
