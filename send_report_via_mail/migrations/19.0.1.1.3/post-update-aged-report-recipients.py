from odoo import SUPERUSER_ID, api


EMAIL_CC = (
    'moustapha@primeshiftuae.com, '
    'souzan@primeshiftuae.com, '
    'nour@primeshiftuae.com'
)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['ir.config_parameter'].set_param(
        'send_report_via_mail.email_cc',
        EMAIL_CC,
    )
    env.ref('send_report_via_mail.report_cron').write({'active': False})
