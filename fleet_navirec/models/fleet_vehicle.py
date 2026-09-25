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
    navirec_last_speed = fields.Float(string="Navirec Speed (km/h)", readonly=True)
    navirec_ignition = fields.Boolean(readonly=True)
    navirec_odometer = fields.Float(string="Navirec Odometer (km)", readonly=True)
    navirec_last_state_time = fields.Datetime(readonly=True)
    navirec_is_stale = fields.Boolean(compute="_compute_navirec_is_stale")

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
                    vehicle.write({"navirec_uuid": False})
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
                vehicles.filtered("navirec_uuid").write({"navirec_uuid": False})
                continue
            vehicle = vehicles
            new_uuid = item.get("id") if item else False
            if vehicle.navirec_uuid != new_uuid:
                vehicle.write({"navirec_uuid": new_uuid})
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
            "navirec_last_speed": state.get("speed") or 0.0,
            "navirec_ignition": bool(state.get("ignition")),
        }
        parsed_time = self._parse_state_time(state.get("time"))
        if parsed_time:
            vals["navirec_last_state_time"] = parsed_time
        if self._valid_coordinates(coords):
            vals.update({
                "navirec_last_lon": coords[0],
                "navirec_last_lat": coords[1],
                "navirec_has_position": True,
            })
        if state.get("total_distance") is not None:
            try:
                vals["navirec_odometer"] = float(state["total_distance"]) / 1000.0
            except (TypeError, ValueError):
                _logger.warning(
                    "Ignoring invalid Navirec total_distance for %s: %r",
                    self.display_name,
                    state.get("total_distance"),
                )
        self.write(vals)

    @api.model
    def _cron_navirec_match_vehicles(self):
        client = self._navirec_client()
        if not client:
            return
        try:
            self._navirec_match_vehicles(client)
        except NavirecAPIError:
            _logger.exception("Navirec vehicle matching failed")

    @api.model
    def _cron_navirec_sync_states(self):
        client = self._navirec_client()
        if not client:
            return
        try:
            self._navirec_sync_states(client)
        except NavirecAPIError:
            _logger.exception("Navirec state sync failed")

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
            if states:
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
