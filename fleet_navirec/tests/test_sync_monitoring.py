from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
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
        self.assertIn("action_navirec_mapping_audit", arch)
        self.assertIn("action_navirec_open_sync_logs", arch)
        self.assertIn("navirec_use_area_names", arch)
        self.assertIn("navirec_vehicle_url_template", arch)

    def test_mapping_audit_action_returns_operational_summary(self):
        settings = self.env["res.config.settings"].create({
            "navirec_api_token": "token",
        })
        vehicle_type = type(self.env["fleet.vehicle"])
        fake_client = object()
        audit = {
            "remote_vehicle_count": 45,
            "odoo_plate_candidate_count": 47,
            "matched_count": 44,
            "currently_mapped_count": 44,
            "unmatched_remote": ["T111AAA"],
            "odoo_only": ["TRAILER1"],
            "duplicate_remote": [],
            "duplicate_local": [],
        }
        with (
            patch.object(vehicle_type, "_navirec_client", return_value=fake_client),
            patch.object(vehicle_type, "_navirec_build_mapping_audit", return_value=audit),
        ):
            action = settings.action_navirec_mapping_audit()

        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "warning")
        self.assertIn("Navirec vehicles: 45", action["params"]["message"])
        self.assertIn("Deterministic matches: 44", action["params"]["message"])
        self.assertIn("T111AAA", action["params"]["message"])

    def test_vehicle_deep_link_template_requires_https_and_uuid_placeholder(self):
        with self.env.cr.savepoint(), self.assertRaises(ValidationError):
            self.env["res.config.settings"].create({
                "navirec_vehicle_url_template": "http://app.navirec.com/vehicle/{uuid}",
            })
        with self.env.cr.savepoint(), self.assertRaises(ValidationError):
            self.env["res.config.settings"].create({
                "navirec_vehicle_url_template": "https://app.navirec.com/vehicle/",
            })
        settings = self.env["res.config.settings"].create({
            "navirec_vehicle_url_template": "https://app.navirec.com/vehicle/{uuid}",
        })
        self.assertTrue(settings.exists())

    def test_test_connection_checks_areas_only_when_enabled(self):
        settings = self.env["res.config.settings"].create({
            "navirec_api_token": "token",
            "navirec_account_id": "acct",
            "navirec_use_area_names": True,
        })
        with patch(
            "odoo.addons.fleet_navirec.models.res_config_settings.NavirecClient"
        ) as client_class:
            client = client_class.return_value
            action = settings.action_navirec_test_connection()
        client.test_connection.assert_called_once_with()
        client.get_areas.assert_called_once_with(account_id="acct")
        self.assertEqual(action["params"]["type"], "success")

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
