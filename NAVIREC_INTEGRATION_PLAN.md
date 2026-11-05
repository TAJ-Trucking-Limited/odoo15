# Navirec Integration Plan — `fleet_navirec` (Odoo 19)

> **Validated against the live TAJ account on 2026-09-18** — see Section 0.

## 0. Live-account validation (2026-09-18)

Verified with the client's web credentials (Username `Taj`) against `https://api.navirec.com/`:

| Fact | Result |
|---|---|
| Credentials | Valid — login works |
| Account | `TAJ Trucking`, id `458b8f60-b567-4dfe-a0d5-c1fc8a45b1dc`, timezone `Africa/Dar_es_Salaam` |
| Vehicles | **45 trucks**, all with clean TZ plates (`T135EMD`, `T478EKL`, …) — direct match candidates for `fleet.vehicle.license_plate` |
| Live states | 45/45 reporting; 44/45 GPS fix < 1h old, 0 stale > 24h |
| Odometer (`total_distance`) | present on 38/45 vehicles |
| Fuel level | **only 2/45 vehicles** — fuel reports need sensor calibration on the Navirec side before Odoo can use them |
| Ignition / speed / heading / satellites / voltage | present on all |
| Human-readable address | **not in the API** — `location` is GeoJSON only. Phase-1 options: show coordinates + Google Maps link; later: reverse-geocode (OSM Nominatim) or use Navirec `areas` names |

**Auth findings (important):**
- `POST /authenticate/` with username/password is **rejected for integrations on API ≥ 1.19.0** ("Please use the Navirec web application to generate an API token").
- It still works when requesting `version=1.18.0` — useful for development/exploration only.
- **The module must use a proper API token** (Navirec web app → Profile → Account settings → API access → create token → assign permissions to the integration user). The client must generate this token and hand it over; the module's token field + Test Connection button are built for exactly that.
- API surface is large (330 endpoints) and covers every later phase of the TAJ spec: `trips` (12), `areas`/`area_groups` (geofences), `webhooks` + `notification_webhooks` (push events — preferred over Odoo-side geofence math for Phase 4), `odometer_readings`, `tankings`/`fuel_cards`, `reports` (40), `drivers`/`ibuttons`.

**Security note:** the web password was shared in chat. Treat it as exposed: rotate it after the API token is created, and never store it in code, config parameters, or git. The module stores only the API token.

## 1. Goal

Sync Navirec GPS fleet-tracking data into Odoo's `fleet.vehicle`:

- Match Navirec vehicles to Odoo vehicles by license plate.
- Show each vehicle's last known GPS position, speed, ignition state, and odometer on the vehicle form.
- Provide a "View on Google Maps" button for the last known position.

~~Credentials (API token + account ID) are held by the client and will be provided later, so the module is built and tested against mocked API responses, with a "Test Connection" button for validating the real token on arrival.~~
> **Updated testing strategy:** A Postman collection covering **all Phase-1 endpoints** (`/accounts/`, `/vehicles/` incl. pagination, `/last_vehicle_states/`, plus a deliberate `401` bad-token capture) will be created. The user puts the real token in Postman, runs the collection, and provides the response payloads as test fixtures — subject to these guardrails:
> 1. **Fixtures are sanitized before committing** — coordinates jittered/zeroed, plates replaced (`T000XXX`), UUIDs regenerated, account ID replaced. Live fleet positions must never land in git.
> 2. **Token hygiene** — the user exports response bodies only; the Postman environment/collection containing the token is never shared or committed.
> 3. **Mocked tests stay** — real fixtures cannot reproduce `429`/`401`/network failures/pagination `Link` headers on demand; error-path tests keep mocking `requests`. Real fixtures complement, not replace, mocked tests.

### Approved decisions

| Decision | Choice |
|---|---|
| Scope | Vehicles + live location only (no trips/fuel/streaming in phase 1) |
| Target version | Odoo 19 |
| Module layout | New standalone module `fleet_navirec`, depends only on `fleet` |
| Map | Google Maps link button (no Enterprise `web_map`, no custom OWL map widget) |
| Unmatched Navirec vehicles | Match only — never auto-create `fleet.vehicle` records |
| Credentials | ~~Not yet available — mock-based tests, validate later via Test Connection~~<br>**Updated:** Token tested via Postman collection (Phase-1 endpoints) by the user; real responses provided as **sanitized** fixtures that **complement** the mocked error-path tests |

---

## 2. Navirec API summary (official docs)

- **Base URL:** `https://api.navirec.com/`
- **Auth:** `Authorization: Token <api_token>` — token generated in the Navirec web app: Profile → Account settings → API access. Permissions must be assigned to the integration user after token creation.
- **Versioning:** ~~`Accept: application/json; version=1.51.0` (pinned, configurable in settings)~~
  - *Updated:* `DEFAULT_API_VERSION = "1.51.0"` constant in `navirec_api.py` — users never configure it. An optional **hidden** `fleet_navirec.api_version` config parameter (not shown in the settings UI) overrides the constant as an emergency escape hatch if Navirec deprecates a version before a module release ships.
  - *Rejected:* "auto-detect from API responses" — `/environment/` exposes no version field, response `Content-Type` only echoes the *requested* version, and Navirec documents versionless calls as developer-convenience-only (production requests **must** pin a version).
- **User-Agent:** required, identifiable, e.g. `TajOdoo/1.0.0 (fleet_navirec)`
- **Time zones:** `Accept-Timezone: <IANA tz>` to avoid UTC conversion (e.g. `Asia/Dubai`)
- **Pagination:** Link-header pagination (`rel="next"` / `rel="prev"`), `page_size` up to 1000. Always include a unique field in `ordering` (e.g. `ordering=id`).
- **Rate limits (relevant):**
  - authenticated sessions: 120/min, 1800/hour
  - `last_vehicle_states`: 24/min
  - `trips`: 24/min, 200/hour (phase 2 only)
  - On `429`: wait `Retry-After` seconds, then retry.
- **Endpoints used in phase 1:**
  - `GET /vehicles/` — paginated vehicle list; `id` (UUID), `registration` (plate), `name`
  - `GET /last_vehicle_states/` — latest state per vehicle; params: `account`, `vehicle(s)`, `vehicle_groups`, `active`, `updated_at__gt`
- **Vehicle state payload (key fields):**
  - `vehicle`: hyperlinked ref `https://api.navirec.com/vehicles/<uuid>/` → UUID extracted from URL
  - `time`: ISO-8601 timestamp of the GPS fix
  - `location`: GeoJSON Point, `coordinates: [longitude, latitude]` — note the order
  - `speed` (km/h), `heading`, `altitude`, `ignition` (bool)
  - `total_distance` (meters, lifetime odometer), `fuel_level` (%), `total_engine_time` (s)

### Out of scope (phase 2+, documented for later)

- Real-time NDJSON stream: `GET /streams/vehicle_states/` (6/min connection limit, ~1h connection lifetime, reconnect with `updated_at__gt`)
- Report streams: `POST /reports/trips/{id}/stream/`, `/reports/tankings/{id}/stream/` — NDJSON, cursor pagination, ≥10s between page requests
- Feeding trip distance/fuel into the Taj Trip Profit report (`send_report_via_mail`)
- Automatic `fleet.vehicle.odometer` log entries

---

## 3. Module structure

```
fleet_navirec/
├── __init__.py
├── __manifest__.py              # version 19.0.1.0.0, depends: ['fleet'], license LGPL-3
├── models/
│   ├── __init__.py
│   ├── navirec_api.py           # plain-Python API client (no Odoo models)
│   ├── fleet_vehicle.py         # fleet.vehicle fields + sync logic
│   └── res_config_settings.py   # settings fields + Test Connection
├── data/
│   └── ir_cron_data.xml         # 2 scheduled jobs
├── views/
│   ├── fleet_vehicle_views.xml  # "Navirec Tracking" group + smart buttons
│   └── res_config_settings_views.xml
└── tests/
    ├── __init__.py
    ├── fixtures/
    │   ├── vehicles_page1.json      # sample /vehicles/ page
    │   ├── vehicles_page2.json      # sample next page (Link header)
    │   └── last_vehicle_states.json # sample /last_vehicle_states/
    ├── test_navirec_api.py      # client tests (requests mocked)
    └── test_vehicle_sync.py     # matching/sync tests (client mocked)
```

No `security/` folder: no new models — fields ride on `fleet.vehicle` (inherits fleet ACLs) and config lives in `ir.config_parameter` (restricted to Settings admins).

---

## 4. API client — `models/navirec_api.py`

Plain-Python wrapper around `requests` (bundled with Odoo 19). No Odoo model inheritance; instantiated on demand with config values.

```python
class NavirecAPIError(Exception): ...

class NavirecClient:
    def __init__(self, api_url, token, version=None, user_agent="TajOdoo/1.0.0 (fleet_navirec)", timezone=None): ...
    # version resolution: hidden fleet_navirec.api_version param → DEFAULT_API_VERSION constant ("1.51.0")

    def get_vehicles(self): ...            # GET /vehicles/?ordering=id — follows Link rel="next"
    def get_last_vehicle_states(self, account_id=None): ...  # GET /last_vehicle_states/
    def test_connection(self): ...         # GET /vehicles/?page_size=1 → bool / raises
```

Behavior:

- Sends `Authorization`, `Accept` (with version), `User-Agent`, `Accept-Timezone` headers on every call.
- `get_vehicles()` walks Link-header pagination until no `rel="next"`, yields a single combined list.
- On `429`: sleeps `Retry-After` seconds and retries **once**; on repeated failure raises `NavirecAPIError`.
- On `401/403`: raises `NavirecAPIError("Invalid token or missing permissions")` → surfaced as `UserError`.
- On network/5xx errors: raises `NavirecAPIError` with the original error chained.
- Helper `vehicle_uuid_from_url(url)` extracts the UUID from hyperlinked vehicle refs.

---

## 5. Settings — `models/res_config_settings.py` + view

Stored as `ir.config_parameter` via `config_parameter=` attribute:

| Param key | Field | Default |
|---|---|---|
| `fleet_navirec.api_token` | Char (password widget) | — |
| `fleet_navirec.account_id` | Char | empty (all permitted accounts) |
| `fleet_navirec.api_url` | Char | `https://api.navirec.com/` |
| ~~`fleet_navirec.api_version`~~ | ~~Char~~ | ~~`1.51.0`~~ |
| *(Removed from settings UI)* | *Code constant + hidden override* | *`DEFAULT_API_VERSION = "1.51.0"` in code; optional hidden `fleet_navirec.api_version` param overrides it for emergencies — never shown to users* |
| `fleet_navirec.timezone` | Char | empty → UTC |

- **"Test Connection" button** → `NavirecClient.test_connection()`; shows a success/warning notification with the error detail.
- Settings block rendered inside Fleet settings (`fleet` app → Configuration → Settings), guarded so the block shows even with an empty token.

---

## 6. `fleet.vehicle` fields — `models/fleet_vehicle.py`

| Field | Type | Source / notes |
|---|---|---|
| `navirec_uuid` | Char, index=True, copy=False | `/vehicles/` → `id`; the match key |
| `navirec_last_lat` | Float digits=(10,7) | state `location.coordinates[1]` |
| `navirec_last_lon` | Float digits=(10,7) | state `location.coordinates[0]` (GeoJSON is lon-first) |
| `navirec_last_speed` | Float (km/h) | state `speed` |
| `navirec_ignition` | Boolean | state `ignition` |
| `navirec_odometer` | Float (km) | state `total_distance` / 1000 |
| `navirec_last_state_time` | Datetime | state `time` (converted per configured tz) |
| `navirec_is_stale` | Boolean, computed | `True` when `navirec_last_state_time` is older than the stale threshold (spec §4/§15 — never show an old position as live) |

Plus a **stale-data warning** banner on the vehicle form when `navirec_is_stale` (threshold configurable, default 24h — live data showed 44/45 trucks update hourly, so 24h is a safe default).

Methods:

- `action_view_on_map()` — opens `https://www.google.com/maps/search/?api=1&query=<lat>,<lon>` in a new tab (`ir.actions.act_url`). Button disabled when no coordinates.
- `action_open_in_navirec()` — opens `https://app.navirec.com/` vehicle detail for `navirec_uuid` (spec §4: detailed telematics history stays in Navirec).
- `action_navirec_sync_now()` — single-vehicle refresh: calls `/last_vehicle_states/?vehicle=<uuid>`, writes fields, shows notification.
- `_navirec_match_vehicles(client)` — fetches `/vehicles/`, normalizes plates (`upper()`, strip spaces/dashes/dots) on both sides, stamps `navirec_uuid` on matches; clears `navirec_uuid` on vehicles no longer present. Unmatched Navirec vehicles are logged, never created.
- `_navirec_sync_states(client)` — fetches `/last_vehicle_states/` (with `account` filter if configured), maps each state to its vehicle via extracted UUID, writes the six state fields in one `write()` per vehicle.

All sync entry points catch `NavirecAPIError`, log via `_logger.exception`, and never propagate out of cron.

---

## 7. Scheduled jobs — `data/ir_cron_data.xml`

| Cron | Interval | Action |
|---|---|---|
| `ir_cron_navirec_sync_states` | every 5 minutes | `_navirec_sync_states` — well inside the 24/min rate limit |
| `ir_cron_navirec_sync_vehicles` | daily | `_navirec_match_vehicles` — refreshes plate matching |

Both crons skip silently (with a debug log) when no API token is configured.

---

## 8. Views

### `views/fleet_vehicle_views.xml` (inherit `fleet.fleet_vehicle_view_form`)

- New group **"Navirec Tracking"** (attrs invisible unless `navirec_uuid`), containing:
  - `navirec_uuid` (readonly, copy button)
  - Ignition badge: `navirec_ignition` as a boolean toggle / badge widget
  - `navirec_last_lat`, `navirec_last_lon`, `navirec_last_speed`, `navirec_odometer`, `navirec_last_state_time` (all readonly)
- Header buttons: **Sync Now** (`action_navirec_sync_now`), **View on Map** (`action_view_on_map`)
- Optional list-view columns: `navirec_ignition`, `navirec_last_state_time`

### `views/res_config_settings_views.xml`

- Inherit Fleet settings form; add **Navirec** block with the five config fields + Test Connection button.

---

## 9. Tests (Postman collection + sanitized fixtures + mocked error paths)

~~Fixtures are copied from Navirec's documented payloads (realistic field names, UUID-shaped ids, GeoJSON coordinates).~~
> **Updated:** A Postman collection covering **all Phase-1 endpoints** is created first. The user runs it with the live API token in Postman and exports the response bodies, which become `fixtures/*.json` **after sanitization** (coordinates zeroed, plates/UUIDs/account ID replaced — see §1 guardrails). These real fixtures drive the payload-shape tests; the error-path tests below keep mocking `requests` (real `429`/`401`/pagination failures cannot be reproduced on demand).

### `tests/test_navirec_api.py` — client unit tests (`requests` mocked — error paths)

- Correct headers sent (auth, version, user-agent, timezone)
- Link-header pagination followed across 2 pages → combined result
- `429` → waits `Retry-After`, retries once, succeeds
- Repeated `429` / `401` / network error → raises `NavirecAPIError`
- UUID extraction from hyperlinked vehicle refs

### `tests/test_vehicle_sync.py` — sync tests (client mocked, payloads from sanitized real fixtures)

- Plate normalization: `ABC-123`, `abc 123`, `ABC123` all match
- Match stamps `navirec_uuid`; unmatched Odoo vehicles untouched; unmatched Navirec vehicles logged, not created
- State sync writes lat/lon (correct GeoJSON order), speed, ignition, odometer m→km, timestamp
- State for unknown UUID is skipped without error
- Crons no-op cleanly when token is empty

Run: `odoo -d test_db -i fleet_navirec --test-enable --stop-after-init` (plus the module's tests via `--test-tags /fleet_navirec`).

---

## 10. Go-live checklist (once the client provides credentials)

0. **Postman validation (user):** import the Phase-1 Postman collection, set the token in the Postman environment (never shared), run `/accounts/`, `/vehicles/`, `/last_vehicle_states/` + a deliberate bad-token `401` capture, export response bodies → sanitize → commit as `fixtures/*.json`.
1. In Navirec web app: Profile → Account settings → **API access** → create token, assign permissions to the integration user.
2. In Odoo: Fleet → Configuration → Settings → Navirec block → paste token (+ optional account ID), save.
3. Click **Test Connection** → expect success notification.
4. Run **Navirec: sync vehicles** cron manually once (Settings → Technical → Scheduled Actions) → verify `navirec_uuid` stamped on matched vehicles.
5. Run **Navirec: sync states** cron manually → verify positions/speed/ignition appear on vehicle forms.
6. Check logs for unmatched Navirec plates; fix plate typos in Odoo if vehicles are missing.

---

## 11. Phase 2 candidates (not in this build)

Ordered per the TAJ internal spec (TAJ_Tracking_Internal_Technical_Spec_v1, §18) — only Phase 1 is client-approved:

- **Phase 1 remainder — per-client tracking report:** scheduled Fleet Position report (3× daily) filtered per customer with configurable fields, emailed via the existing `send_report_via_mail` patterns. This is the client-facing deliverable of the Client Agreement and should be specced as its own task once the connector lands.
- **Phase 2 — Transport Operations module:** trips, cargo, customers, time-based truck/trailer/driver assignment history.
- **Phase 3 — Milestones:** route templates, milestone sequences, manual workflow.
- **Phase 4 — GPS automation:** milestone arrival/departure detection. Prefer Navirec `webhooks`/`notification_webhooks` (push on area entry/exit) over Odoo-side geofence math, per spec §8; fall back to coordinate+radius checks in the sync cron.
- **Phase 5/6 — Reports & alerts:** trip progress, delays, border performance, utilization, stale-GPS alerts, scheduled emails.
- **Phase 7 — Financial:** trip costs/revenue/profitability, tying into `move_invoice_line` / Trip Profit report.
- Trips + fuel from `/reports/trips/{id}/stream/` and `/reports/tankings/{id}/stream/` (blocked on fuel sensors: only 2/45 trucks report fuel today).
- Real-time positions via `/streams/vehicle_states/` NDJSON stream (long-lived connection worker, reconnect with `updated_at__gt`).
- Automatic `fleet.vehicle.odometer` log entries from `total_distance` (38/45 trucks report it).
- Embedded map view (Leaflet OWL component or Enterprise `web_map`).
- Human-readable location names: reverse-geocode coordinates (OSM Nominatim) or map Navirec `areas` names — the API has no address field.
