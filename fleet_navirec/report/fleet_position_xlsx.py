from odoo import models


class FleetPositionXlsx(models.AbstractModel):
    _name = "report.fleet_navirec.fleet_position_xlsx"
    _inherit = "report.report_xlsx.abstract"
    _description = "Fleet Position Report (XLSX)"

    def generate_xlsx_report(self, workbook, data, subscriptions):
        for index, subscription in enumerate(subscriptions, start=1):
            sheet = workbook.add_worksheet(f"Fleet Position {index}")
            columns = subscription._report_columns()
            header_format = workbook.add_format({"bold": True})
            cell_format = workbook.add_format({"text_wrap": True, "valign": "top"})
            widths = [len(str(label)) for _key, label in columns]
            sheet.freeze_panes(1, 0)
            for col, (_key, label) in enumerate(columns):
                # write_string: a location like "=1+1" must stay text,
                # never become a spreadsheet formula.
                sheet.write_string(0, col, str(label), header_format)
            for row_idx, row in enumerate(subscription._prepare_report_rows(), start=1):
                for col, (key, _label) in enumerate(columns):
                    value = str(row.get(key) or "")
                    sheet.write_string(row_idx, col, value, cell_format)
                    widths[col] = max(widths[col], len(value))
            for col, width in enumerate(widths):
                sheet.set_column(col, col, min(max(width + 2, 12), 60))
