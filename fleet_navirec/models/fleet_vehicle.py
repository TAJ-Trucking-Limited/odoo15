import logging
from datetime import datetime, timezone

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .navirec_api import (
    DEFAULT_API_URL,
    NavirecAPIError,
    NavirecClient,
    vehicle_uuid_from_url,
)

_logger = logging.getLogger(__name__)


class FleetVehicle(models.Model):
    _inherit = "fleet.vehicle"

    navirec_uuid = fields.Char(index=True, copy=False, readonly=True)
    navirec_last_lat = fields.Float(digits=(10, 7), readonly=True)
    navirec_last_lon = fields.Float(digits=(10, 7), readonly=True)
    navirec_has_position = fields.Boolean(readonly=True, copy=False)
    navirec_location_name = fields.Char(readonly=True, copy=False)
    navirec_last_speed = fields.Float(string="Navirec Speed (km/h)", readonly=True)
    navirec_has_speed = fields.Boolean(readonly=True, copy=False)
    navirec_ignition = fields.Boolean(readonly=True)
    navirec_has_ignition = fields.Boolean(readonly=True, copy=False)
    navirec_odometer = fields.Float(string="Navirec Odometer (km)", readonly=True)
    navirec_has_odometer = fields.Boolean(readonly=True, copy=False)
    navirec_last_state_time = fields.Datetime(readonly=True)
    navirec_is_stale = fields.Boolean(compute="_compute_navirec_is_stale")
    navirec_integration_status = fields.Selection(
        [
            ("not_mapped", "Not Mapped"),
            ("awaiting_data", "Awaiting GPS Data"),
            ("connected", "Connected"),
            ("stale", "Stale"),
        ],
        compute="_compute_navirec_integration_status",
        string="Integration Status",
    )
    navirec_position_display = fields.Char(
        compute="_compute_navirec_display_values",
        string="Current Position",
    )
    navirec_speed_display = fields.Char(
        compute="_compute_navirec_display_values",
        string="Speed",
    )
    navirec_ignition_display = fields.Char(
        compute="_compute_navirec_display_values",
        string="Ignition",
    )
    navirec_odometer_display = fields.Char(
        compute="_compute_navirec_display_values",
        string="Odometer",
    )
    navirec_movement_state = fields.Selection(
        [
            ("unknown", "Not Available"),
            ("moving", "Moving"),
            ("idling", "Idling"),
            ("stopped", "Stopped"),
        ],
        compute="_compute_navirec_display_values",
        string="Movement",
    )

    @api.depends("navirec_last_state_time")
    def _compute_navirec_is_stale(self):
        params = self.env["ir.config_parameter"].sudo()
        try:
            stale_hours = int(
                params.get_param("fleet_navirec.stale_hours", 24) or 24
            )
        except (TypeError, ValueError):
            stale_hours = 24
        now = fields.Datetime.now()
        for vehicle in self:
            vehicle.navirec_is_stale = bool(
                vehicle.navirec_last_state_time
                and (now - vehicle.navirec_last_state_time).total_seconds()
                > stale_hours * 3600
            )

    @api.depends("navirec_uuid", "navirec_last_state_time", "navirec_is_stale")
    def _compute_navirec_integration_status(self):
        for vehicle in self:
            if not vehicle.navirec_uuid:
                vehicle.navirec_integration_status = "not_mapped"
            elif not vehicle.navirec_last_state_time:
                vehicle.navirec_integration_status = "awaiting_data"
            elif vehicle.navirec_is_stale:
                vehicle.navirec_integration_status = "stale"
            else:
                vehicle.navirec_integration_status = "connected"

    @api.depends(
        "navirec_location_name",
        "navirec_has_position",
        "navirec_last_lat",
        "navirec_last_lon",
        "navirec_has_speed",
        "navirec_last_speed",
        "navirec_has_ignition",
        "navirec_ignition",
        "navirec_has_odometer",
        "navirec_odometer",
    )
    def _compute_navirec_display_values(self):
        for vehicle in self:
            if vehicle.navirec_location_name:
                vehicle.navirec_position_display = vehicle.navirec_location_name
            elif vehicle.navirec_has_position:
                vehicle.navirec_position_display = (
                    f"{vehicle.navirec_last_lat:.7f}, "
                    f"{vehicle.navirec_last_lon:.7f}"
                )
            else:
                vehicle.navirec_position_display = _("Not available")

            vehicle.navirec_speed_display = (
                _("%(speed).1f km/h") % {"speed": vehicle.navirec_last_speed}
                if vehicle.navirec_has_speed
                else _("Not available")
            )
            vehicle.navirec_ignition_display = (
                _("On") if vehicle.navirec_ignition else _("Off")
            ) if vehicle.navirec_has_ignition else _("Not available")
            vehicle.navirec_odometer_display = (
                _("%(odometer).1f km") % {"odometer": vehicle.navirec_odometer}
                if vehicle.navirec_has_odometer
                else _("Not available")
            )

            if not vehicle.navirec_has_speed:
                vehicle.navirec_movement_state = "unknown"
            elif vehicle.navirec_last_speed > 1.0:
                vehicle.navirec_movement_state = "moving"
            elif vehicle.navirec_has_ignition and vehicle.navirec_ignition:
                vehicle.navirec_movement_state = "idling"
            elif vehicle.navirec_has_ignition:
                vehicle.navirec_movement_state = "stopped"
            else:
                vehicle.navirec_movement_state = "unknown"

    @api.model
    def _navirec_client(self):
        params = self.env["ir.config_parameter"].sudo()
        token = params.get_param("fleet_navirec.api_token")
        if not token:
            return False
        return NavirecClient(
            api_url=params.get_param("fleet_navirec.api_url", DEFAULT_API_URL),
            token=token,
            version=params.get_param("fleet_navirec.api_version") or None,
            timezone=params.get_param("fleet_navirec.timezone") or None,
        )

    @staticmethod
    def _normalize_plate(value):
        return "".join(ch for ch in (value or "").upper() if ch.isalnum())

    @staticmethod
    def _parse_state_time(value):
        if not value:
            return False
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            _logger.warning("Ignoring invalid Navirec state time: %r", value)
            return False
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    @staticmethod
    def _valid_coordinates(coords):
        return (
            isinstance(coords, (list, tuple))
            and len(coords) >= 2
            and isinstance(coords[0], (int, float))
            and not isinstance(coords[0], bool)
            and isinstance(coords[1], (int, float))
            and not isinstance(coords[1], bool)
        )

    @staticmethod
    def _navirec_reset_tracking_values(navirec_uuid=False):
        return {
            "navirec_uuid": navirec_uuid,
            "navirec_last_lat": 0.0,
            "navirec_last_lon": 0.0,
            "navirec_has_position": False,
            "navirec_location_name": False,
            "navirec_last_speed": 0.0,
            "navirec_has_speed": False,
            "navirec_ignition": False,
            "navirec_has_ignition": False,
            "navirec_odometer": 0.0,
            "navirec_has_odometer": False,
            "navirec_last_state_time": False,
        }

    @api.model
    def _navirec_match_vehicles(self, client):
        remote_by_plate = {}
        duplicate_remote = set()
        for item in client.get_vehicles():
            plate = self._normalize_plate(item.get("registration"))
            if not plate:
                continue
            if plate in remote_by_plate:
                duplicate_remote.add(plate)
                continue
            remote_by_plate[plate] = item
        for plate in duplicate_remote:
            remote_by_plate.pop(plate, None)
        if duplicate_remote:
            _logger.warning(
                "Skipping duplicate normalized Navirec plates: %s",
                ", ".join(sorted(duplicate_remote)),
            )

        local_by_plate = {}
        for vehicle in self.search([]):
            plate = self._normalize_plate(vehicle.license_plate)
            if not plate:
                if vehicle.navirec_uuid:
                    vehicle.write(self._navirec_reset_tracking_values())
                continue
            local_by_plate.setdefault(plate, self.browse())
            local_by_plate[plate] |= vehicle

        matched_ids = set()
        for plate, vehicles in local_by_plate.items():
            item = remote_by_plate.get(plate)
            if len(vehicles) > 1:
                _logger.warning(
                    "Skipping duplicate normalized Odoo plate %s on vehicle IDs %s",
                    plate,
                    vehicles.ids,
                )
                mapped = vehicles.filtered("navirec_uuid")
                if mapped:
                    mapped.write(self._navirec_reset_tracking_values())
                continue
            vehicle = vehicles
            new_uuid = item.get("id") if item else False
            if vehicle.navirec_uuid != new_uuid:
                vehicle.write(
                    self._navirec_reset_tracking_values(new_uuid)
                )
            if new_uuid:
                matched_ids.add(new_uuid)

        unmatched = [
            item.get("registration")
            for item in remote_by_plate.values()
            if item.get("id") not in matched_ids
        ]
        if unmatched:
            _logger.info(
                "Unmatched Navirec vehicles: %s",
                ", ".join(filter(None, unmatched)),
            )
        return len(matched_ids)

    @api.model
    def _navirec_sync_states(self, client):
        params = self.env["ir.config_parameter"].sudo()
        account_id = params.get_param("fleet_navirec.account_id") or None
        states = client.get_last_vehicle_states(account_id=account_id)
        vehicles = {
            vehicle.navirec_uuid: vehicle
            for vehicle in self.search([("navirec_uuid", "!=", False)])
        }
        updated = 0
        for state in states:
            uuid = (
                vehicle_uuid_from_url(state.get("vehicle"))
                or state.get("vehicle_id")
            )
            vehicle = vehicles.get(uuid)
            if not vehicle:
                _logger.debug(
                    "Skipping Navirec state for unknown vehicle UUID %s", uuid
                )
                continue
            vehicle._write_navirec_state(state)
            updated += 1
        return updated

    def _write_navirec_state(self, state):
        self.ensure_one()
        coords = ((state.get("location") or {}).get("coordinates") or [])
        vals = {
            "navirec_has_position": False,
            "navirec_location_name": False,
            "navirec_has_speed": False,
            "navirec_has_ignition": False,
            "navirec_has_odometer": False,
        }

        parsed_time = self._parse_state_time(state.get("time"))
        if parsed_time:
            vals["navirec_last_state_time"] = parsed_time

        if self._valid_coordinates(coords):
            vals.update({
                "navirec_last_lon": float(coords[0]),
                "navirec_last_lat": float(coords[1]),
                "navirec_has_position": True,
            })

        speed = state.get("speed")
        if speed is not None:
            try:
                vals["navirec_last_speed"] = float(speed)
                vals["navirec_has_speed"] = True
            except (TypeError, ValueError):
                _logger.warning(
                    "Ignoring invalid Navirec speed for %s: %r",
                    self.display_name,
                    speed,
                )

        ignition = state.get("ignition")
        if isinstance(ignition, bool):
            vals["navirec_ignition"] = ignition
            vals["navirec_has_ignition"] = True
        elif isinstance(ignition, int) and ignition in (0, 1):
            vals["navirec_ignition"] = bool(ignition)
            vals["navirec_has_ignition"] = True

        if state.get("total_distance") is not None:
            try:
                vals["navirec_odometer"] = (
                    float(state["total_distance"]) / 1000.0
                )
                vals["navirec_has_odometer"] = True
            except (TypeError, ValueError):
                _logger.warning(
                    "Ignoring invalid Navirec total_distance for %s: %r",
                    self.display_name,
                    state.get("total_distance"),
                )

        self.write(vals)

    @api.model
    def _navirec_record_sync_result(
        self,
        operation,
        started_at,
        status,
        records_updated=0,
        message=None,
    ):
        finished_at = fields.Datetime.now()
        self.env["fleet.navirec.sync.log"].sudo().create({
            "operation": operation,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "records_updated": records_updated,
            "message": message or False,
        })

        params = self.env["ir.config_parameter"].sudo()
        key = (
            "vehicle_match"
            if operation == "vehicle_match"
            else "state_sync"
        )
        params.set_param(
            f"fleet_navirec.last_{key}_attempt",
            fields.Datetime.to_string(started_at),
        )
        if status == "success":
            params.set_param(
                f"fleet_navirec.last_{key}_success",
                fields.Datetime.to_string(finished_at),
            )
            params.set_param(
                f"fleet_navirec.last_{key}_count",
                str(records_updated),
            )
            params.set_param(f"fleet_navirec.last_{key}_error", "")
            params.set_param(f"fleet_navirec.last_{key}_error_time", "")
        else:
            params.set_param(
                f"fleet_navirec.last_{key}_error",
                message or _("Unknown Navirec synchronization error"),
            )
            params.set_param(
                f"fleet_navirec.last_{key}_error_time",
                fields.Datetime.to_string(finished_at),
            )

    @api.model
    def _cron_navirec_match_vehicles(self):
        client = self._navirec_client()
        if not client:
            return
        started_at = fields.Datetime.now()
        try:
            matched = self._navirec_match_vehicles(client)
        except NavirecAPIError as exc:
            self._navirec_record_sync_result(
                "vehicle_match",
                started_at,
                "error",
                message=str(exc),
            )
            _logger.exception("Navirec vehicle matching failed")
            return
        self._navirec_record_sync_result(
            "vehicle_match",
            started_at,
            "success",
            records_updated=matched,
        )

    @api.model
    def _cron_navirec_sync_states(self):
        client = self._navirec_client()
        if not client:
            return
        started_at = fields.Datetime.now()
        try:
            updated = self._navirec_sync_states(client)
        except NavirecAPIError as exc:
            self._navirec_record_sync_result(
                "state_sync",
                started_at,
                "error",
                message=str(exc),
            )
            _logger.exception("Navirec state sync failed")
            return
        self._navirec_record_sync_result(
            "state_sync",
            started_at,
            "success",
            records_updated=updated,
        )

    def action_navirec_sync_now(self):
        self.ensure_one()
        client = self._navirec_client()
        if not client:
            raise UserError(_("Navirec API token is not configured."))
        if not self.navirec_uuid:
            raise UserError(_("This vehicle is not matched to Navirec yet."))
        try:
            states = client.get_last_vehicle_states(
                vehicle_id=self.navirec_uuid
            )
            if not states:
                raise UserError(
                    _("Navirec returned no GPS state for this vehicle.")
                )
            self._write_navirec_state(states[0])
        except NavirecAPIError as exc:
            raise UserError(str(exc)) from exc
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Navirec"),
                "message": _("Vehicle refreshed."),
                "type": "success",
            },
        }

    def action_view_on_map(self):
        self.ensure_one()
        if not self.navirec_has_position:
            raise UserError(
                _("No Navirec coordinates are available for this vehicle.")
            )
        url = (
            "https://www.google.com/maps/search/?api=1&query="
            f"{self.navirec_last_lat},{self.navirec_last_lon}"
        )
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}

    def action_open_in_navirec(self):
        self.ensure_one()
        if not self.navirec_uuid:
            raise UserError(_("This vehicle is not matched to Navirec yet."))
        return {
            "type": "ir.actions.act_url",
            "url": "https://app.navirec.com/",
            "target": "new",
        }
