"""Expose the assigned truck's Navirec GPS position on trip email updates.

Email rendering reads only the already-synchronized vehicle fields. No synchronous
HTTP/API call to Navirec is permitted during email generation.
"""

import math
from odoo import _, fields, models


def _is_valid_coord(lat, lon):
    """Validate geographical coordinates including legitimate zero values."""
    if lat is None or lon is None:
        return False
    if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))):
        return False
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


class TajTransportTrip(models.Model):
    _inherit = "taj.transport.trip"

    def _get_email_update_position_data(self):
        """Override base milestone position provider with live Navirec telematics if available."""
        self.ensure_one()
        vehicle = self.current_vehicle_id
        if vehicle and getattr(vehicle, "navirec_has_position", False):
            lat = vehicle.navirec_last_lat
            lon = vehicle.navirec_last_lon
            is_stale_flag = getattr(vehicle, "navirec_is_stale", False)
            last_gps_time = getattr(vehicle, "navirec_last_state_time", False)
            valid_coord = _is_valid_coord(lat, lon)

            # Fresh GPS strictly requires: not marked stale, timestamp present, and valid coordinates
            is_fresh = bool(not is_stale_flag and last_gps_time and valid_coord)

            display_pos = (
                vehicle.navirec_position_display
                or vehicle.navirec_location_name
                or (f"{lat:.5f}, {lon:.5f}" if valid_coord else _("Unknown coordinates"))
            )

            if is_fresh:
                map_url = f"https://www.google.com/maps?q={lat},{lon}"
                return {
                    "label": display_pos,
                    "source": "navirec",
                    "updated_at": last_gps_time,
                    "latitude": lat,
                    "longitude": lon,
                    "map_url": map_url,
                    "is_stale": False,
                    "notice": False,
                }
            else:
                # Stale GPS or missing timestamp:
                # Prioritize latest actual milestone or planned origin as primary location.
                # The Google Maps link must correspond to the location being presented.
                # Never attach stale GPS coordinates as the map link for the milestone or planned origin.
                base_data = super()._get_email_update_position_data()
                gps_time_str = fields.Datetime.to_string(last_gps_time) if last_gps_time else _("timestamp missing")
                base_source = base_data.get("source") or "planned"

                if base_source == "milestone":
                    notice = _(
                        "Live GPS fix is stale (%s recorded at %s UTC). Showing last confirmed checkpoint."
                    ) % (display_pos, gps_time_str)
                else:
                    notice = _(
                        "Live GPS fix is stale (%s recorded at %s UTC). Showing planned origin location (planned location, not a confirmed checkpoint or live position)."
                    ) % (display_pos, gps_time_str)

                return {
                    "label": base_data.get("label"),
                    "source": f"{base_source}_with_stale_gps",
                    "updated_at": base_data.get("updated_at"),
                    "latitude": base_data.get("latitude") or False,
                    "longitude": base_data.get("longitude") or False,
                    "map_url": base_data.get("map_url") or False,
                    "is_stale": True,
                    "notice": notice,
                }

        return super()._get_email_update_position_data()
