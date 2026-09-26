import logging
import math
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .navirec_api import (
    DEFAULT_API_URL,
    NavirecAPIError,
    NavirecClient,
    public_navirec_error,
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
            user_id=params.get_param("fleet_navirec.integration_user_id") or None,
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
    def _navirec_point_in_ring(lon, lat, ring):
        """Return whether a GeoJSON [lon, lat] point is inside a linear ring."""
        if not isinstance(ring, (list, tuple)) or len(ring) < 3:
            return False
        inside = False
        j = len(ring) - 1
        for i, point in enumerate(ring):
            previous = ring[j]
            j = i
            if not (
                isinstance(point, (list, tuple)) and len(point) >= 2
                and isinstance(previous, (list, tuple)) and len(previous) >= 2
            ):
                continue
            try:
                xi, yi = float(point[0]), float(point[1])
                xj, yj = float(previous[0]), float(previous[1])
            except (TypeError, ValueError):
                continue
            if (yi > lat) != (yj > lat):
                x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
                if lon < x_cross:
                    inside = not inside
        return inside

    @classmethod
    def _navirec_point_in_polygon(cls, lon, lat, rings):
        if not isinstance(rings, (list, tuple)) or not rings:
            return False
        if not cls._navirec_point_in_ring(lon, lat, rings[0]):
            return False
        return not any(
            cls._navirec_point_in_ring(lon, lat, hole)
            for hole in rings[1:]
        )

    @staticmethod
    def _navirec_distance_m(lon1, lat1, lon2, lat2):
        """Haversine distance in meters for Navirec circle areas."""
        radius = 6371008.8
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = (
            math.sin(dphi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        )
        return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @classmethod
    def _navirec_point_in_area(cls, lon, lat, area):
        if not isinstance(area, dict) or area.get("active") is False:
            return False
        area_type = area.get("type")
        center = ((area.get("center") or {}).get("coordinates") or [])
        radius = area.get("radius")
        if area_type in ("circle", "point") and cls._valid_coordinates(center):
            try:
                radius_m = float(radius)
            except (TypeError, ValueError):
                radius_m = 0.0
            if radius_m > 0:
                return cls._navirec_distance_m(
                    lon, lat, float(center[0]), float(center[1])
                ) <= radius_m
            # A point without a radius has no documented containment boundary.
            return False

        shape = area.get("shape") or {}
        coordinates = shape.get("coordinates") or []
        geometry_type = shape.get("type") or area_type
        if geometry_type == "Polygon" or area_type == "polygon":
            return cls._navirec_point_in_polygon(lon, lat, coordinates)
        if geometry_type == "MultiPolygon" or area_type == "multipolygon":
            return any(
                cls._navirec_point_in_polygon(lon, lat, polygon)
                for polygon in coordinates
            )
        return False

    @classmethod
    def _navirec_area_name_for_coordinates(cls, areas, lon, lat):
        matches = []
        priority = {"point_of_interest": 0, "geofence": 1}
        for area in areas or []:
            if area.get("category") == "country":
                continue
            if not area.get("name"):
                continue
            if cls._navirec_point_in_area(lon, lat, area):
                matches.append((
                    priority.get(area.get("category"), 2),
                    area.get("name"),
                ))
        if not matches:
            return False
        matches.sort(key=lambda item: (item[0], item[1].casefold()))
        return matches[0][1]

    @staticmethod
    def _navirec_state_uuid(state):
        return (
            vehicle_uuid_from_url((state or {}).get("vehicle"))
            or (state or {}).get("vehicle_id")
        )

    @staticmethod
    def _navirec_datetime_to_iso_utc(value):
        if not value:
            return False
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.isoformat().replace("+00:00", "Z")

    @classmethod
    def _navirec_trip_address_index(cls, trips):
        by_vehicle = {}
        for trip in trips or []:
            uuid = cls._navirec_state_uuid(trip)
            if not uuid:
                continue
            trip_time = cls._parse_state_time(
                trip.get("end_time") or trip.get("start_time")
            )
            for prefix in ("end", "start"):
                address = (trip.get(f"{prefix}_address") or "").strip()
                coords = (
                    (trip.get(f"{prefix}_location") or {}).get("coordinates") or []
                )
                if not address or not cls._valid_coordinates(coords):
                    continue
                by_vehicle.setdefault(uuid, []).append((
                    trip_time,
                    {
                        "address": address,
                        "location": {"coordinates": coords},
                    },
                ))
        for uuid, items in by_vehicle.items():
            items.sort(
                key=lambda item: item[0] or datetime.min,
                reverse=True,
            )
            by_vehicle[uuid] = [item[1] for item in items]
        return by_vehicle

    @classmethod
    def _navirec_trip_address_for_state(
        cls,
        state,
        trips_by_vehicle,
        max_distance_m=1000.0,
    ):
        coords = ((state.get("location") or {}).get("coordinates") or [])
        uuid = cls._navirec_state_uuid(state)
        if not uuid or not cls._valid_coordinates(coords):
            return False
        lon, lat = float(coords[0]), float(coords[1])
        for candidate in trips_by_vehicle.get(uuid, []):
            candidate_coords = (
                (candidate.get("location") or {}).get("coordinates") or []
            )
            if not cls._valid_coordinates(candidate_coords):
                continue
            if cls._navirec_distance_m(
                lon,
                lat,
                float(candidate_coords[0]),
                float(candidate_coords[1]),
            ) <= max_distance_m:
                address = (candidate.get("address") or "").strip()
                if address:
                    return address
        return False

    @classmethod
    def _navirec_can_reuse_location_name(
        cls,
        vehicle,
        coords,
        max_distance_m=250.0,
    ):
        if not (
            vehicle
            and vehicle.navirec_location_name
            and vehicle.navirec_has_position
            and cls._valid_coordinates(coords)
        ):
            return False
        return cls._navirec_distance_m(
            float(coords[0]),
            float(coords[1]),
            vehicle.navirec_last_lon,
            vehicle.navirec_last_lat,
        ) <= max_distance_m

    @api.model
    def _navirec_location_names_for_states(
        self,
        client,
        states,
        account_id=None,
        vehicles_by_uuid=None,
        trip_lookback_hours=168,
    ):
        """Resolve human-readable names without making state sync fragile.

        Priority: Navirec Area/POI -> cached nearby name -> nearby Navirec trip
        address -> optional Navirec reverse geocoder -> coordinates in the
        display layer. Vehicle Events are intentionally excluded because the
        live integration token rejects its documented filters.
        """
        params = self.env["ir.config_parameter"].sudo()
        enabled = str(
            params.get_param("fleet_navirec.use_area_names", "False") or "False"
        ).lower() in ("1", "true", "yes")
        if not enabled:
            return {}

        states = list(states or [])
        vehicles_by_uuid = vehicles_by_uuid or {}
        names = {}
        areas = []
        try:
            areas = client.get_areas(account_id=account_id)
        except NavirecAPIError as exc:
            _logger.warning(
                "Navirec area-name enrichment unavailable: %s",
                exc,
            )

        needs_trip_lookup = []
        for state in states:
            uuid = self._navirec_state_uuid(state)
            coords = ((state.get("location") or {}).get("coordinates") or [])
            if not uuid or not self._valid_coordinates(coords):
                continue
            lon, lat = float(coords[0]), float(coords[1])
            name = self._navirec_area_name_for_coordinates(areas, lon, lat)
            if name:
                names[uuid] = name
                continue
            vehicle = vehicles_by_uuid.get(uuid)
            if self._navirec_can_reuse_location_name(vehicle, coords):
                names[uuid] = vehicle.navirec_location_name
                continue
            needs_trip_lookup.append(state)

        if not needs_trip_lookup:
            return names

        parsed_times = [
            self._parse_state_time(state.get("time"))
            for state in needs_trip_lookup
        ]
        parsed_times = [value for value in parsed_times if value]
        reference_time = max(parsed_times) if parsed_times else fields.Datetime.now()
        trip_since = reference_time - timedelta(hours=trip_lookback_hours)
        trip_until = reference_time + timedelta(minutes=5)
        trip_vehicle_ids = [
            self._navirec_state_uuid(state)
            for state in needs_trip_lookup
            if self._navirec_state_uuid(state)
        ]
        try:
            trips = client.get_trips(
                account_id=account_id,
                vehicle_ids=trip_vehicle_ids,
                start_time_gte=self._navirec_datetime_to_iso_utc(trip_since),
                end_time_lte=self._navirec_datetime_to_iso_utc(trip_until),
            )
        except NavirecAPIError as exc:
            _logger.warning(
                "Navirec trip-address enrichment unavailable: %s",
                exc,
            )
            trips = []

        trips_by_vehicle = self._navirec_trip_address_index(trips)
        for state in needs_trip_lookup:
            uuid = self._navirec_state_uuid(state)
            address = self._navirec_trip_address_for_state(
                state,
                trips_by_vehicle,
            )
            if address:
                names[uuid] = address

        needs_geocode = [
            state
            for state in needs_trip_lookup
            if self._navirec_state_uuid(state) not in names
        ]
        if not needs_geocode:
            return names

        geocode = self._navirec_reverse_geocode_states(
            client,
            needs_geocode,
            account_id=account_id,
        )
        names.update(geocode)
        return names

    @classmethod
    def _navirec_reverse_geocode_states(cls, client, states, account_id=None):
        """Resolve names from Navirec's geocoder for points nothing else matched.

        One configuration lookup per sync. Points within 250 m share one
        reverse lookup. A geocoder failure leaves those vehicles on coordinates.
        ponytail: worst case is one reverse call per vehicle that moved more
        than 250 m. Upgrade path: a batch reverse endpoint if Navirec adds one.
        """
        get_context = getattr(client, "get_geocoding_context", None)
        reverse = getattr(client, "reverse_geocode", None)
        if not get_context or not reverse:
            return {}
        try:
            context = get_context(account_id=account_id)
        except NavirecAPIError as exc:
            log = _logger.info if exc.status_code == 403 else _logger.warning
            log("Navirec reverse geocoding unavailable: %s", exc)
            client.geocode_error = public_navirec_error(exc)
            return {}
        if (
            not isinstance(context, tuple)
            or len(context) != 2
            or not isinstance(context[1], dict)
        ):
            return {}
        base_url, query_params = context
        names = {}
        # (lon, lat, address or False). False remembers a lookup with no address.
        attempted = []
        for state in states:
            uuid = cls._navirec_state_uuid(state)
            coords = ((state.get("location") or {}).get("coordinates") or [])
            if not uuid or not cls._valid_coordinates(coords):
                continue
            lon, lat = float(coords[0]), float(coords[1])
            reused = False
            for previous_lon, previous_lat, previous_address in attempted:
                if cls._navirec_distance_m(
                    lon, lat, previous_lon, previous_lat
                ) > 250.0:
                    continue
                if previous_address:
                    names[uuid] = previous_address
                reused = True
                break
            if reused:
                continue
            try:
                address = reverse(lon, lat, base_url, query_params)
            except NavirecAPIError as exc:
                _logger.warning(
                    "Navirec reverse geocoding stopped: %s",
                    exc,
                )
                client.geocode_error = public_navirec_error(exc)
                break
            if not isinstance(address, str):
                address = False
            else:
                address = address.strip() or False
            attempted.append((lon, lat, address))
            if address:
                names[uuid] = address
        if attempted and not names and not getattr(client, "geocode_error", None):
            client.geocode_error = "Navirec geocoder returned no address"
        _logger.info(
            "Navirec reverse geocode: unresolved=%s named=%s lookups=%s",
            len(states),
            len(names),
            len(attempted),
        )
        return names

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
    def _navirec_build_mapping_audit(self, client):
        """Return a read-only plate-mapping audit for Operations review."""
        remote_by_plate = {}
        for item in client.get_vehicles():
            plate = self._normalize_plate(item.get("registration"))
            if plate:
                remote_by_plate.setdefault(plate, []).append(item)

        local_by_plate = {}
        for vehicle in self.search([]):
            plate = self._normalize_plate(vehicle.license_plate)
            if plate:
                local_by_plate.setdefault(plate, self.browse())
                local_by_plate[plate] |= vehicle

        duplicate_remote = sorted(
            plate for plate, items in remote_by_plate.items() if len(items) > 1
        )
        duplicate_local = sorted(
            plate for plate, vehicles in local_by_plate.items() if len(vehicles) > 1
        )
        duplicate_remote_set = set(duplicate_remote)
        duplicate_local_set = set(duplicate_local)
        remote_unique = set(remote_by_plate) - duplicate_remote_set
        local_unique = set(local_by_plate) - duplicate_local_set
        matched_plates = sorted(remote_unique & local_unique)

        unmatched_remote = sorted(
            (
                (remote_by_plate[plate][0].get("registration") or plate)
                for plate in (remote_unique - local_unique)
            ),
            key=str.casefold,
        )
        odoo_only = sorted(
            (
                (local_by_plate[plate][:1].license_plate or plate)
                for plate in (local_unique - remote_unique)
            ),
            key=str.casefold,
        )
        return {
            "remote_vehicle_count": sum(len(items) for items in remote_by_plate.values()),
            "odoo_plate_candidate_count": sum(len(records) for records in local_by_plate.values()),
            "matched_count": len(matched_plates),
            "currently_mapped_count": self.search_count([("navirec_uuid", "!=", False)]),
            "unmatched_remote": unmatched_remote,
            "odoo_only": odoo_only,
            "duplicate_remote": duplicate_remote,
            "duplicate_local": duplicate_local,
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
        location_names = self._navirec_location_names_for_states(
            client,
            states,
            account_id=account_id,
            vehicles_by_uuid=vehicles,
        )
        updated = 0
        for state in states:
            uuid = self._navirec_state_uuid(state)
            vehicle = vehicles.get(uuid)
            if not vehicle:
                _logger.debug(
                    "Skipping Navirec state for unknown vehicle UUID %s", uuid
                )
                continue
            vehicle._write_navirec_state(
                state,
                location_name=location_names.get(uuid),
            )
            updated += 1
        return updated

    def _write_navirec_state(self, state, location_name=False):
        self.ensure_one()
        coords = ((state.get("location") or {}).get("coordinates") or [])
        vals = {
            "navirec_has_position": False,
            "navirec_location_name": location_name or False,
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
            message=getattr(client, "geocode_error", None) or None,
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
            state = states[0]
            params = self.env["ir.config_parameter"].sudo()
            account_id = params.get_param("fleet_navirec.account_id") or None
            location_names = self._navirec_location_names_for_states(
                client,
                [state],
                account_id=account_id,
                vehicles_by_uuid={self.navirec_uuid: self},
                trip_lookback_hours=720,
            )
            self._write_navirec_state(
                state,
                location_name=location_names.get(self.navirec_uuid),
            )
        except NavirecAPIError as exc:
            raise UserError(str(exc)) from exc
        note = getattr(client, "geocode_error", None)
        if note and not self.navirec_location_name:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Navirec"),
                    "message": _(
                        "Vehicle refreshed. Readable location unavailable: %s"
                    ) % note,
                    "type": "warning",
                    "sticky": True,
                },
            }
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
        params = self.env["ir.config_parameter"].sudo()
        template = (
            params.get_param("fleet_navirec.vehicle_url_template") or ""
        ).strip()
        if template.startswith("https://") and "{uuid}" in template:
            url = template.replace("{uuid}", quote(self.navirec_uuid, safe=""))
        else:
            url = "https://app.navirec.com/"
        return {
            "type": "ir.actions.act_url",
            "url": url,
            "target": "new",
        }
