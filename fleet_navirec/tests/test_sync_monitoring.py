from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestNavirecSyncMonitoring(TransactionCase):

    def test_success_result_creates_log_and_summary(self):
        started_at = fields.Datetime.now()
        self.env["fleet.vehicle"]._navirec_record_sync_result(
            "state_sync",
            started_at,
            "success",
            records_updated=45,
        )

        log = self.env["fleet.navirec.sync.log"].search(
            [("operation", "=", "state_sync")],
            limit=1,
        )
        self.assertEqual(log.status, "success")
        self.assertEqual(log.records_updated, 45)
        self.assertTrue(log.finished_at)

        params = self.env["ir.config_parameter"].sudo()
        self.assertEqual(
            params.get_param("fleet_navirec.last_state_sync_count"),
            "45",
        )
        self.assertTrue(
            params.get_param("fleet_navirec.last_state_sync_success")
        )
        self.assertFalse(
            params.get_param("fleet_navirec.last_state_sync_error")
        )

    def test_error_result_keeps_error_visible(self):
        started_at = fields.Datetime.now()
        self.env["fleet.vehicle"]._navirec_record_sync_result(
            "vehicle_match",
            started_at,
            "error",
            message="Invalid token or missing permissions",
        )

        log = self.env["fleet.navirec.sync.log"].search(
            [("operation", "=", "vehicle_match")],
            limit=1,
        )
        self.assertEqual(log.status, "error")
        self.assertIn("Invalid token", log.message)

        params = self.env["ir.config_parameter"].sudo()
        self.assertEqual(
            params.get_param("fleet_navirec.last_vehicle_match_error"),
            "Invalid token or missing permissions",
        )
        self.assertTrue(
            params.get_param(
                "fleet_navirec.last_vehicle_match_error_time"
            )
        )


    def test_settings_reads_monitoring_summary(self):
        params = self.env["ir.config_parameter"].sudo()
        now = fields.Datetime.now()
        params.set_param(
            "fleet_navirec.last_state_sync_success",
            fields.Datetime.to_string(now),
        )
        params.set_param("fleet_navirec.last_state_sync_count", "44")
        params.set_param(
            "fleet_navirec.last_state_sync_error",
            "Temporary API error",
        )
        params.set_param(
            "fleet_navirec.last_state_sync_error_time",
            fields.Datetime.to_string(now),
        )

        settings = self.env["res.config.settings"].create({})
        settings._compute_navirec_monitoring()

        self.assertEqual(settings.navirec_last_state_sync_count, 44)
        self.assertEqual(
            settings.navirec_last_state_sync_error,
            "Temporary API error",
        )
        self.assertEqual(
            fields.Datetime.to_string(
                settings.navirec_last_state_sync_success
            ),
            fields.Datetime.to_string(now),
        )

    def test_settings_view_exposes_monitoring_actions(self):
        view = self.env.ref(
            "fleet_navirec.res_config_settings_view_form_navirec"
        )
        arch = view.arch_db
        self.assertIn("action_navirec_sync_all_now", arch)
        self.assertIn("action_navirec_match_now", arch)
        self.assertIn("action_navirec_open_sync_logs", arch)

    def test_sync_now_persists_unsaved_navirec_settings(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.api_token", "")
        params.set_param("fleet_navirec.last_state_sync_error", "")
        params.set_param("fleet_navirec.last_state_sync_count", "0")

        settings = self.env["res.config.settings"].create({
            "navirec_api_token": "form-token",
            "navirec_account_id": "form-account",
        })
        vehicle_type = type(self.env["fleet.vehicle"])

        with patch.object(
            vehicle_type,
            "_cron_navirec_sync_states",
            return_value=None,
        ):
            action = settings.action_navirec_sync_all_now()

        self.assertEqual(
            params.get_param("fleet_navirec.api_token"),
            "form-token",
        )
        self.assertEqual(
            params.get_param("fleet_navirec.account_id"),
            "form-account",
        )
        self.assertEqual(action["params"]["type"], "success")
