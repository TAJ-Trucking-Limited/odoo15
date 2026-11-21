"""Preview and download the customer report for the current set of trips."""

from base64 import b64encode
from datetime import datetime

from odoo import api, fields, models

from ..models.transport_trip_email import CUSTOMER_REPORT_FILENAME, REPORT_XLSX_MIMETYPE

MILESTONE_REPORT_FILENAME = "Milestone_Comparison_{:%Y-%m-%d_%H%M%S}.xlsx"


class TajTransportCustomerReportPreview(models.TransientModel):
    _name = "taj.transport.customer.report.preview"
    _description = "Customer Trip Report Preview"

    customer_id = fields.Many2one("res.partner", string="Customer", readonly=True)
    email_to = fields.Char(string="To", readonly=True)
    email_cc = fields.Char(string="Cc", readonly=True)
    comparison_only = fields.Boolean(readonly=True)
    trips_html = fields.Html(readonly=True)
    checkpoints_html = fields.Html(readonly=True)
    comparison_html = fields.Html(readonly=True)
    report_file = fields.Binary(attachment=False, readonly=True)
    filename = fields.Char(readonly=True)

    @api.model
    def _create_preview(self, trips, timezone, comparison_only=False):
        report = trips._prepare_customer_trip_report(timezone)
        filename = MILESTONE_REPORT_FILENAME if comparison_only else CUSTOMER_REPORT_FILENAME
        values = {
            "comparison_only": comparison_only,
            "comparison_html": self._render_table(report["comparison"]),
            "report_file": b64encode(trips._render_customer_trip_workbook(report, comparison_only)),
            "filename": filename.format(fields.Datetime.now()),
        }
        if not comparison_only:
            customer = trips.commercial_partner_id
            primary, cc = customer._customer_trip_report_recipients()
            values.update({
                "customer_id": customer.id,
                "email_to": primary,
                "email_cc": ", ".join(cc),
                "trips_html": self._render_table(report["trips"]),
                "checkpoints_html": self._render_table(report["checkpoints"]),
            })
        return self.create(values)

    @api.model
    def _render_table(self, table):
        def display(value):
            if isinstance(value, datetime):
                return value.strftime("%Y-%m-%d %H:%M")
            if isinstance(value, float):
                return f"{value:+.2f}" if value else "0.00"
            return value or ""

        return self.env["ir.qweb"]._render("taj_transport_operations.customer_trip_report_table", {
            "headers": table["headers"],
            "rows": [[display(value) for value in row] for row in table["rows"]],
            "groups": table.get("groups"),
            "time_heading": table.get("time_heading"),
        })

    def action_download_xlsx(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_url",
            "url": (
                f"/web/content/{self._name}/{self.id}/report_file"
                f"?filename_field=filename&download=true&mimetype={REPORT_XLSX_MIMETYPE}"
            ),
            "target": "download",
        }
