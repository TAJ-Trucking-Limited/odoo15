"""Read-only, bounded live diagnosis. Execute in a fresh Odoo staging shell.

Never log tokens/keys/raw configuration, change records, or send reports.
"""
import ast
import math
import os
from pathlib import Path

from odoo.addons.fleet_navirec.models import navirec_api


def diagnose(odoo_env):
    db_name = odoo_env.cr.dbname
    if "19-upgrade" not in db_name and "19_upgrade" not in db_name:
        raise RuntimeError("This diagnostic is restricted to the 19_upgrade staging database")
    print("NAVIREC READ-ONLY DIAGNOSTIC")
    print("DATABASE", db_name)
    root = Path(navirec_api.__file__).resolve().parents[1]
    manifest = ast.literal_eval((root / "__manifest__.py").read_text())
    print("CODE_VERSION", manifest.get("version"))
    params = odoo_env["ir.config_parameter"].sudo()
    account = params.get_param("fleet_navirec.account_id") or None
    print("ACCOUNT_CONFIGURED", bool(account))
    print("READABLE_LOCATION_SETTING", params.get_param("fleet_navirec.use_area_names"))
    client = odoo_env["fleet.vehicle"]._navirec_client()
    if not client:
        raise RuntimeError("Navirec token is not configured")
    print("TOKEN_USER_UUID_PRESENT", bool(client._token_user_id()))
    print("EXPLICIT_USER_UUID_CONFIGURED", bool(client.user_id))
    # Show no identity/token value. Resolve exactly the same owner as production.
    try:
        client._configuration_user_id(account)
        print("USER_IDENTITY_RESOLUTION", "OK")
    except navirec_api.NavirecAPIError as exc:
        print("USER_IDENTITY_RESOLUTION", navirec_api.public_navirec_error(exc))

    vehicle_id = int(os.environ.get("NAVIREC_DIAG_VEHICLE_ID", "39"))
    vehicle = odoo_env["fleet.vehicle"].browse(vehicle_id).exists()
    if not vehicle or not vehicle.navirec_uuid:
        raise RuntimeError("Selected vehicle is missing or not mapped")
    print("ODOO_VEHICLE_ID", vehicle_id)
    print("READABLE_NAME_STORED", bool(vehicle.navirec_location_name))

    def rows(data):
        items = data if isinstance(data, list) else (
            data.get("results") if isinstance(data, dict) else None
        )
        return items if isinstance(items, list) else []

    def first_page(label, endpoint, query):
        # Intentionally do not follow pagination: each check reads one tiny page.
        try:
            response = client._request("GET", endpoint, params=query)
            data = response.json()
        except navirec_api.NavirecAPIError as exc:
            print(label, "HTTP", exc.status_code, navirec_api.public_navirec_error(exc))
            return None
        items = rows(data)
        print(label, "HTTP", response.status_code, "FIRST_PAGE_RECORDS", len(items),
              "FILTER_NAMES", ",".join(sorted(query)))
        return data

    state_data = first_page("LAST_STATE", "last_vehicle_states/", {
        "vehicle": vehicle.navirec_uuid, "page_size": 1,
    })
    state_list = rows(state_data)
    state = next((item for item in state_list if isinstance(item, dict)
                  and vehicle._navirec_state_uuid(item) == vehicle.navirec_uuid), None)
    print(
        "VEHICLE_EVENT_ENRICHMENT",
        "DISABLED: live integration token rejected vehicle/vehicles/account filters",
    )

    try:
        base_url, context = client.get_geocoding_context(account_id=account)
    except navirec_api.NavirecAPIError as exc:
        print("GEOCODING_CONFIGURATION", "HTTP", exc.status_code,
              navirec_api.public_navirec_error(exc))
        print("REVERSE_LOOKUP", "NOT ATTEMPTED: configuration unavailable")
        return
    print("GEOCODING_CONFIGURATION", "OK", "KEY_PRESENT", bool(context.get("key")))
    coords = ((state or {}).get("location") or {}).get("coordinates")
    if not vehicle._valid_coordinates(coords):
        print("REVERSE_LOOKUP", "SKIPPED: no valid current GPS")
        return
    lon, lat = coords[:2]
    if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
        print("REVERSE_LOOKUP", "SKIPPED: invalid GPS range")
        return
    try:
        address = client.reverse_geocode(lon, lat, base_url, context)
    except navirec_api.NavirecAPIError as exc:
        print("REVERSE_LOOKUP", "HTTP", exc.status_code, navirec_api.public_navirec_error(exc))
        return
    print("REVERSE_ADDRESS", address or "NO ADDRESS RETURNED")
    print("NO ODOO RECORDS MODIFIED")


if "env" not in globals():
    raise RuntimeError("Run this file inside a fresh Odoo shell, not plain Python")
diagnose(env)
