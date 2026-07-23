from types import SimpleNamespace

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.send_report_via_mail.models.send_mail import (
    ALL_ACCOUNT_CODES,
    DETAILS,
    FUEL_CODES,
    GOING_CODES,
    GOING_DETAILS,
    RETURN_CODES,
    RETURN_DETAILS,
)


@tagged('post_install', '-at_install')
class TestSendMail(TransactionCase):
    def test_account_code_mapping_contract(self):
        self.assertEqual(len(GOING_DETAILS), 26)
        self.assertEqual(len(RETURN_DETAILS), 26)
        self.assertEqual(len(set(ALL_ACCOUNT_CODES)), 52)
        self.assertEqual(
            set(ALL_ACCOUNT_CODES),
            set(FUEL_CODES) | set(GOING_CODES) | set(RETURN_CODES),
        )
        self.assertTrue({
            '500077',
            '500100',
            '500101',
            '500120',
            '500121',
        }.issubset(ALL_ACCOUNT_CODES))

    def test_detail_mapping_matches_trip_profit_fields(self):
        model_fields = self.env['report.trip.profit']._fields

        for _code, _report_key, field_name in DETAILS:
            self.assertIn(field_name, model_fields)

    def test_duplicate_direction_aliases_keep_the_same_amount(self):
        debit_by_code = {code: 0.0 for code in ALL_ACCOUNT_CODES}
        debit_by_code['500044'] = 125.0

        values = self.env['report.send.mail']._detail_values(debit_by_code)

        self.assertEqual(values['Transit_fees_Bond'], 125.0)
        self.assertEqual(values['Transit_fees_Bond_going'], 125.0)

    def test_trip_profit_values_preserve_percentage_and_odd_field_names(self):
        row = {
            'license_plate': 'TEST-1',
            'trip': 'Going',
            'size': '40FT',
            'operating_income': 200.0,
            'total_going': 50.0,
            'total_return': 0.0,
            'total_fuel': 25.0,
            'expenses': 75.0,
            'cross_profit': 125.0,
            **{
                report_key: 10.0
                for _code, report_key, _field_name in DETAILS
            },
        }
        order_line = SimpleNamespace(
            order_id=SimpleNamespace(
                id=10,
                date_order=SimpleNamespace(date=lambda: '2026-07-22'),
            ),
            product_template_id=SimpleNamespace(id=20),
        )

        values = self.env['report.send.mail']._trip_profit_values(
            order_line,
            row,
        )

        self.assertEqual(values['percentage'], 62.5)
        self.assertEqual(values['transit_fees_tollRoad'], 10.0)
        self.assertEqual(values['return_fees_container_tAX_going'], 10.0)

    def test_missing_recipient_configuration_fails_before_report_export(self):
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_param('send_report_via_mail.email_to', '')

        with self.assertRaises(UserError):
            self.env['report.send.mail'].send_email_with_pdf_attach()
