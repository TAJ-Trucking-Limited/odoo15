import base64
import datetime

from odoo import Command, _, fields, models
from odoo.exceptions import UserError


FUEL_CODES = ('500073', '500074', '500075', '500076', '500077')
GOING_CODES = (
    '500037', '500038', '500039', '500041', '500042', '500045', '500046',
    '500047', '500049', '500050', '500053', '500055', '500056', '500058',
    '500059', '500069', '500096', '500044', '500048', '500054', '500102',
    '500103', '500104', '500105', '500111', '500113',
)
RETURN_CODES = (
    '500061', '500062', '500063', '500064', '500065', '500066', '500067',
    '500068', '500070', '500098', '500110', '500112', '50070', '50080',
    '510071', '511071', '512071', '500044', '500048', '500054', '500102',
    '500103', '500104', '500105', '500111', '500113', '500100', '500101',
    '500120', '500121',
)

FUEL_DETAILS = (
    ('500073', 'mbeya', 'mbeya'),
    ('500076', 'kibaha', 'kibaha'),
    ('500074', 'morogoro', 'morogoro'),
    ('500075', 'tunduma', 'tunduma'),
)
GOING_DETAILS = (
    ('500037', 'Transit_fees_Documentation_Fees', 'transit_fees_documentation_fees'),
    ('500038', 'Transit_fees_Levy_Council_Fee_Nakonde', 'transit_fees_levy_council_fee_nakonde'),
    (
        '500039',
        'Transit_fees_Levy_Council_Fee_Kapiri',
        'transit_fees_levy_council_fee_kapiri',
    ),
    (
        '500041',
        'Transit_fees_Parking_Security_Fees_going',
        'transit_fees_parking_security_fees_going',
    ),
    ('500042', 'Transit_fees_Road_Permit', 'transit_fees_road_permit'),
    ('500045', 'Transit_fees_Electronic_Seal', 'transit_fees_electronic_seal'),
    ('500046', 'Transit_fees_Border_Fees', 'transit_fees_border_fees'),
    ('500047', 'Transit_fees_First_Entry', 'transit_fees_first_entry'),
    ('500049', 'Transit_fees_Lashing_Fees', 'transit_fees_lashing_fees'),
    ('500050', 'Transit_fees_Abnormalm_Signage', 'transit_fees_abnormalm_signage'),
    (
        '500053',
        'Transit_fees_GCLA_Loading_Facilitation_Permit',
        'transit_fees_gcla_loading_facilitation_permit',
    ),
    ('500055', 'Transit_fees_Weighbridge_Fees', 'transit_fees_weighbridge_fees'),
    ('500056', 'Transit_fees_Peage', 'transit_fees_peage'),
    ('500058', 'Transit_fees_Levy_Council_Fee_Tunduma', 'transit_fees_levy_council_fee_tunduma'),
    ('500059', 'Transit_fees_Cargo_Rearrangement', 'transit_fees_cargo_rearrangement'),
    ('500069', 'Transit_fees_Demurrage_Fee', 'transit_fees_demurrage_fee'),
    ('500096', 'Driver_Trip_Allowance_Expense_Transit', 'driver_trip_allowance_expense_transit'),
    ('500044', 'Transit_fees_Bond_going', 'transit_fees_bond_going'),
    ('500048', 'Transit_fees_TollRoad', 'transit_fees_tollRoad'),
    (
        '500054',
        'Transit_fees_GCLA_Loading_Facilitation_Other_going',
        'transit_fees_gcla_loading_facilitation_other_going',
    ),
    ('500102', 'Toll_Gates_going', 'toll_gates_going'),
    ('500103', 'Late_Exit_Note_going', 'late_exit_note_going'),
    ('500104', 'Return_fees_Container_TAX_going', 'return_fees_container_tAX_going'),
    ('500105', 'Carbon_Tax_going', 'carbon_tax_going'),
    ('500111', 'Return_fees_weight_pridje_going', 'return_fees_weight_pridje_going'),
    ('500113', 'wating_charges_going', 'wating_charges_going'),
)
RETURN_DETAILS = (
    ('500061', 'Return_fees_Carrier_License', 'return_fees_carrier_license'),
    ('500062', 'Return_fees_Peage', 'return_fees_peage'),
    ('500063', 'Return_fees_Cargo_Rearrangement', 'return_fees_cargo_rearrangement'),
    (
        '500064',
        'Return_fees_Radiation_Protection_Fee',
        'return_fees_radiation_protection_fee',
    ),
    ('500065', 'Return_fees_Weight_Check_Ndola', 'return_fees_weight_check_ndola'),
    ('500066', 'Return_fees_Parking_Security_Fees_ret', 'return_fees_parking_security_fees_ret'),
    ('500067', 'Return_fees_Levy_Council_Fee_Kapiri', 'return_fees_levy_council_fee_kapiri'),
    (
        '500068',
        'Return_fees_Empty_Container_Offloading_Fees',
        'return_fees_empty_container_offloading_fees',
    ),
    ('500070', 'Return_fees_Visa', 'return_fees_visa'),
    ('500098', 'Driver_Trip_Allowance_Expense_Return', 'driver_trip_allowance_expense_return'),
    ('500110', 'Return_fees_Weight_Check_Tunduma', 'return_fees_weight_check_tunduma'),
    ('500112', 'Return_fees_Chemical_transportation', 'return_fees_chemical_transportation'),
    ('50070', 'Return_fees_Parking_Security_Fees', 'return_fees_parking_security_fees'),
    ('50080', 'Return_fee_Over_Stay', 'return_fee_over_stay'),
    ('510071', 'Return_fees_Entry_Card', 'return_fees_entry_card'),
    ('511071', 'Return_fees_Kanyaka', 'return_fees_kanyaka'),
    ('512071', 'Return_fees_Penalty_over_wight', 'return_fees_penalty_over_wight'),
    ('500044', 'Transit_fees_Bond', 'transit_fees_bond'),
    ('500048', 'Transit_fees_Road_Toll', 'transit_fees_road_toll'),
    (
        '500054',
        'Transit_fees_GCLA_Loading_Facilitation_Other',
        'transit_fees_gcla_loading_facilitation_other',
    ),
    ('500102', 'Toll_Gates', 'toll_gates'),
    ('500103', 'Late_Exit_Note', 'late_exit_note'),
    ('500104', 'Return_fees_Container_TAX', 'return_fees_container_tax'),
    ('500105', 'Carbon_Tax', 'carbon_tax'),
    ('500111', 'Return_fees_weight_pridje', 'return_fees_weight_pridje'),
    ('500113', 'wating_charges', 'wating_charges'),
)
DETAILS = FUEL_DETAILS + GOING_DETAILS + RETURN_DETAILS
ALL_ACCOUNT_CODES = tuple(dict.fromkeys(FUEL_CODES + GOING_CODES + RETURN_CODES))


class ReportSendMail(models.TransientModel):
    _name = 'report.send.mail'
    _description = 'Send Report Pdf Via Mail'

    from_date = fields.Date('From Date')
    to_date = fields.Date('To Date')

    def _get_mail_configuration(self):
        parameters = self.env['ir.config_parameter'].sudo()
        configuration = {
            'email_from': parameters.get_param(
                'send_report_via_mail.email_from',
            ),
            'email_to': parameters.get_param(
                'send_report_via_mail.email_to',
            ),
            'email_cc': parameters.get_param(
                'send_report_via_mail.email_cc',
            ),
        }
        if not configuration['email_from'] or not configuration['email_to']:
            raise UserError(_(
                'Configure the aged-report sender and recipient in the '
                'send_report_via_mail system parameters before sending.',
            ))
        return configuration

    def send_email_with_pdf_attach(self):
        configuration = self._get_mail_configuration()
        attachments = self.env['ir.attachment'].sudo()
        for xml_id, filename in (
            ('account_reports.aged_payable_report', 'aged payable.pdf'),
            ('account_reports.aged_receivable_report', 'aged receivable.pdf'),
        ):
            report = self.env.ref(xml_id)
            options = report.get_options()
            result = report.dispatch_report_action(options, 'export_to_pdf')
            attachments |= self.env['ir.attachment'].sudo().create({
                'name': filename,
                'type': 'binary',
                'datas': base64.b64encode(result['file_content']),
                'mimetype': 'application/pdf',
            })

        mail = self.env['mail.mail'].sudo().create({
            **configuration,
            'subject': 'Aged Reports',
            'body_html': (
                '<p>Dear Mr. Ali,</p>'
                '<p>Please find the attached aged reports for today.</p>'
                '<p>best regards.</p>'
            ),
            'attachment_ids': [Command.set(attachments.ids)],
        })
        mail.send()

    def convert_date_to_datetime(self, from_date, to_date):
        return (
            datetime.datetime.combine(from_date, datetime.datetime.min.time()),
            datetime.datetime.combine(to_date, datetime.datetime.max.time()),
        )

    def _get_sale_orders(self):
        domain = [('company_id', '=', self.env.company.id)]
        if self.from_date and self.to_date:
            from_date, to_date = self.convert_date_to_datetime(
                self.from_date,
                self.to_date,
            )
            domain += [
                ('date_order', '>', from_date),
                ('date_order', '<=', to_date),
            ]
        return self.env['sale.order'].sudo().search(domain)

    @staticmethod
    def _detail_values(debit_by_code):
        return {
            report_key: debit_by_code.get(code, 0.0)
            for code, report_key, _field_name in DETAILS
        }

    def _prepare_profit_rows(self):
        self.ensure_one()
        rows = []
        move_lines_model = self.env['account.move.line'].sudo()
        for order_line in self._get_sale_orders().order_line:
            product_name = order_line.product_template_id.name
            if product_name == 'Down payment':
                continue

            move_lines = move_lines_model.search([
                ('company_id', '=', order_line.order_id.company_id.id),
                ('order_id', '=', order_line.order_id.id),
                ('account_id.code', 'in', ALL_ACCOUNT_CODES),
            ])
            debit_by_code = {code: 0.0 for code in ALL_ACCOUNT_CODES}
            credit_by_code = {code: 0.0 for code in ALL_ACCOUNT_CODES}
            for move_line in move_lines:
                code = move_line.account_id.code
                debit_by_code[code] += move_line.debit
                credit_by_code[code] += move_line.credit

            fuel_lines = move_lines.filtered(
                lambda line: line.account_id.code in FUEL_CODES
            )
            trip = (
                'Going'
                if product_name and 'Dar' in product_name.split('-', 1)[0]
                else 'Return'
            )
            direction_codes = RETURN_CODES if trip == 'Return' else GOING_CODES
            total_fuel_debit = sum(debit_by_code[code] for code in FUEL_CODES)
            total_direction_debit = sum(
                debit_by_code[code] for code in direction_codes
            )
            total_debit = total_fuel_debit + total_direction_debit
            total_credit = (
                sum(credit_by_code[code] for code in FUEL_CODES)
                + sum(credit_by_code[code] for code in direction_codes)
            )
            expenses = total_debit - total_credit
            invoice = order_line.order_id.invoice_ids[:1]
            operating_income = invoice.amount_total_signed if invoice else 0.0

            row = {
                'order_name': order_line.order_id.name,
                'license_plate': order_line.vehicle_id.license_plate,
                'root': product_name,
                'vehicle_id': order_line.vehicle_id.name,
                'product_tmpl_id': product_name,
                'operating_income': operating_income,
                'account_move_line': fuel_lines.ids,
                'total_fuel': total_fuel_debit,
                'date': order_line.order_id.date_order,
                'total_going': total_direction_debit if trip == 'Going' else 0.0,
                'total_return': total_direction_debit if trip == 'Return' else 0.0,
                'total_return_income': sum(
                    debit_by_code[code] for code in RETURN_CODES
                ),
                'total_cost': expenses,
                'cross_profit': operating_income - expenses,
                'size': order_line.size,
                'trip': trip,
                'expenses': expenses,
                **self._detail_values(debit_by_code),
            }
            rows.append((order_line, row))
        return rows

    @staticmethod
    def _trip_profit_values(order_line, row):
        values = {
            'order_id': order_line.order_id.id,
            'date': order_line.order_id.date_order.date(),
            'truck': row['license_plate'],
            'root': order_line.product_template_id.id,
            'trip': row['trip'],
            'size': row['size'],
            'operating_income': row['operating_income'],
            'total_going': row['total_going'],
            'total_return': row['total_return'],
            'total_fuel': row['total_fuel'],
            'expenses': row['expenses'],
            'cross_profit': row['cross_profit'],
            'percentage': (
                round(row['cross_profit'] / row['operating_income'] * 100, 2)
                if row['operating_income']
                else 0
            ),
        }
        values.update({
            field_name: row[report_key]
            for _code, report_key, field_name in DETAILS
        })
        return values

    def send_report(self):
        products = [row for _order_line, row in self._prepare_profit_rows()]
        action = self.env.ref(
            'send_report_via_mail.report_profit_and_loss_excel'
        ).report_action(self, data={'products': products})
        action['close_on_report_download'] = True
        return action

    def view_report(self):
        report_model = self.env['report.trip.profit'].sudo()
        for order_line, row in self._prepare_profit_rows():
            values = self._trip_profit_values(order_line, row)
            existing = report_model.search([
                ('date', '=', values['date']),
                ('order_id', '=', values['order_id']),
            ])
            if existing:
                existing.write({
                    key: value
                    for key, value in values.items()
                    if key != 'date'
                })
            else:
                report_model.create(values)
        return {
            'name': 'Report Trip Profit',
            'type': 'ir.actions.act_window',
            'view_mode': 'list,form',
            'res_model': 'report.trip.profit',
            'context': {'group_by': 'truck'},
        }
