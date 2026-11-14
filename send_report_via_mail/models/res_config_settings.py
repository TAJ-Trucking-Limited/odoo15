from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    send_report_email_from = fields.Char(
        string="Aged Report From",
        config_parameter="send_report_via_mail.email_from",
        help="Sender address used for the daily Aged Payable / Receivable email.",
    )
    send_report_email_to = fields.Char(
        string="Aged Report To",
        config_parameter="send_report_via_mail.email_to",
        help="Primary recipient of the Aged Reports email.",
    )
    send_report_email_cc = fields.Char(
        string="Aged Report Cc",
        config_parameter="send_report_via_mail.email_cc",
        help="Comma-separated CC addresses for the Aged Reports email.",
    )
