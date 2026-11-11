from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestShipmentEntryEdits(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.order = cls.env['sale.order'].create({'partner_id': cls.env.company.partner_id.id})
        cls.product = cls.env['product.product'].create({'name': 'Weight test', 'type': 'service'})
        cls.line = cls.env['sale.order.line'].create({'order_id': cls.order.id, 'product_id': cls.product.id})

    def test_weight_validation_on_create_and_write(self):
        for value, expected in [(' 1250.5 ', '1250.5'), ('0', '0'), ('', False)]:
            self.line.write({'weight': value})
            self.assertEqual(self.line.weight or False, expected)
        for value in ['25T', 'abc', '1,250', '-5', '1e3', 'NaN']:
            with self.subTest(value=value), self.assertRaises(ValidationError), self.env.cr.savepoint():
                self.line.write({'weight': value})
            with self.subTest(create=value), self.assertRaises(ValidationError), self.env.cr.savepoint():
                self.env['sale.order.line'].create({'order_id': self.order.id, 'product_id': self.product.id, 'weight': value})

    def test_eta_migration_preserves_timestamps_and_local_dates(self):
        import runpy
        from odoo.tools import file_path
        migrate = runpy.run_path(file_path('taj_transport_operations/migrations/19.0.1.44.0/pre-migration.py'))['migrate']
        cr = self.env.cr
        cr.execute('SHOW search_path')
        original_path = cr.fetchone()[0]
        try:
            # 2022-01-01 21:30 UTC is Jan 2 in Africa/Dar_es_Salaam (UTC+3)
            # but still Jan 1 in Asia/Damascus (UTC+2 before 2022 DST end).
            cr.execute('CREATE TEMP TABLE sale_order_line (id integer, eta timestamp) ON COMMIT DROP')
            cr.execute(
                "INSERT INTO sale_order_line VALUES "
                "(1, '2022-01-01 21:30:00'), (2, NULL)"
            )
            cr.execute('SET LOCAL search_path TO pg_temp, public')
            migrate(cr, '19.0.1.43.0')
            migrate(cr, '19.0.1.43.0')
            cr.execute('SELECT eta::text, eta_timestamp_backup::text FROM sale_order_line ORDER BY id')
            self.assertEqual(
                cr.fetchall(),
                [('2022-01-02', '2022-01-01 21:30:00'), (None, None)],
            )
            cr.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'sale_order_line' "
                "AND column_name = 'eta'"
            )
            self.assertEqual(cr.fetchone()[0], 'date')
        finally:
            cr.execute('DROP TABLE IF EXISTS pg_temp.sale_order_line')
            cr.execute("SELECT set_config('search_path', %s, true)", (original_path,))

    def test_unchanged_historical_weight_survives_other_writes(self):
        self.env.cr.execute('UPDATE sale_order_line SET weight = %s WHERE id = %s', ('25T', self.line.id))
        self.line.invalidate_recordset(['weight'])
        self.line.write({'etol': '2026-12-01'})
        self.assertEqual(self.line.weight, '25T')
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.line.write({'weight': '25T'})

    def test_numeric_zero_and_decimal_on_create(self):
        for value, expected in [(0, '0'), (' 0.25 ', '0.25'), (None, False)]:
            line = self.env['sale.order.line'].create({
                'order_id': self.order.id, 'product_id': self.product.id, 'weight': value,
            })
            self.assertEqual(line.weight or False, expected)

    def test_file_row_validates_explicit_weight(self):
        self.order.action_confirm()
        row = self.line.transport_file_line_ids
        self.assertEqual(len(row), 1)
        row.write({'weight': ' 1250.5 '})
        self.assertEqual(self.line.weight, '1250.5')
        for value in ['25T', 'abc', '1,250', '-5', '1e3', 'NaN']:
            with self.subTest(value=value), self.assertRaises(ValidationError), self.env.cr.savepoint():
                row.with_context(_file_shipment_write=True).write({'weight': value})
        self.assertEqual(self.line.weight, '1250.5')
