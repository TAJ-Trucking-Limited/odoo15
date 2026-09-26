# TAJ Navirec Fleet Integration

Odoo 19 integration between TAJ Fleet and Navirec. The module synchronizes the
latest telematics state into `fleet.vehicle`, provides operational monitoring,
and delivers client-specific Fleet Position reports on configurable schedules.

## Phase 1 implemented scope

### Connector and fleet mapping

- API-token authentication with a pinned production API version.
- Identifiable User-Agent and optional Navirec timezone header.
- Vehicle pagination and stable UUID extraction.
- HTTP 401/403, 429 retry-once, network, invalid JSON, 4xx and 5xx handling.
- License-plate normalization for deterministic Navirec/Odoo matching.
- Duplicate local and remote normalized plates are never auto-matched.
- Navirec vehicles are never auto-created in Odoo.
- Daily vehicle-matching cron and manual Match Vehicles Now action.
- Read-only Mapping Audit action showing candidate counts, deterministic matches,
  duplicate plates and unmatched candidates before Operations sign-off.

### Current telematics

- Five-minute state synchronization cron and per-vehicle Sync Navirec action.
- Last GPS time, latitude/longitude, speed, ignition and Navirec odometer.
- Availability flags distinguish a missing value from a real zero.
- Moving / Idling / Stopped / Not Available movement state.
- Connected / Stale / Awaiting GPS Data / Not Mapped integration status.
- Configurable stale-GPS threshold and visible stale warning.
- Google Maps action for the latest valid coordinates.
- Dedicated Navirec / Telematics Fleet tab plus list/search integration fields.

### Human-readable location enrichment

The Navirec `last_vehicle_states` resource does not return a human-readable
address. The module can optionally enrich synchronized coordinates using
Navirec's own location data, without sending fleet coordinates to a separate
public geocoding service.

- Enable **Human-readable Navirec locations** in Fleet settings.
- First preference is an active Navirec Area / Point of Interest from `/areas/`.
- Circle, polygon and multipolygon containment is evaluated locally using the
  official Navirec GeoJSON coordinates.
- Point-of-interest matches are preferred over general geofence matches.
- Country areas are deliberately ignored as current-position labels.
- If no area matches, the module checks recent `/vehicle_events/` data and uses
  a Navirec-provided event `address` only when the event GPS point is within
  1 km of the current state.
- If no nearby event address is available, the module checks recent Navirec
  `/trips/` start/end addresses and uses one only when that endpoint is within
  1 km of the current GPS. Manual vehicle refresh uses a longer bounded trip
  lookback so a truck parked for several days can still resolve its last stop.
- If Areas, events, and trips still have no nearby address, the module asks
  Navirec's own reverse geocoder (`/configuration/` supplies the account
  geocoding key; `GET <geocoding_api_url>/reverse/` returns
  `formatted_address`). The key is not stored in the module or in git.
- These distance checks avoid displaying a readable but stale/wrong address from
  an older event or trip elsewhere. A geocoded name is kept only while the
  vehicle stays within 250 meters, and identical nearby points in one sync
  share a single reverse lookup.
- A human-readable name is cached while the vehicle remains within 250 meters,
  which avoids unnecessary repeated event lookups for parked vehicles/GPS jitter.
- Manual **Sync Navirec** and scheduled state synchronization use the same
  enrichment path.
- If Areas, events, trips, or the Navirec geocoder are unavailable, GPS
  synchronization still succeeds and coordinates remain the final fallback.

### Navirec links

`Open Navirec` always works at application level. A verified vehicle-specific
route can be configured without a code release using **Vehicle Deep-Link
Template** in Fleet settings. The value must be HTTPS and contain `{uuid}`.
If no verified template is configured, the action opens `https://app.navirec.com/`.

### Integration monitoring

- Persistent sync history for vehicle matching and state synchronization.
- Last-success time, updated-count and last-error information in Fleet settings.
- Sync All Now, Match Vehicles Now and View Sync Logs actions.
- Sync errors are swallowed by cron after being persisted/logged so a Navirec
  outage does not break unrelated Odoo jobs.
- Sync logs are read-only to Fleet managers.

### Client Tracking Reports

- Fleet-manager-only per-client tracking subscription model.
- Explicit vehicle selection prevents cross-client vehicle disclosure.
- To/CC recipients and IANA timezone per subscription.
- Exactly three distinct configurable local send times per day.
- UTC `next_send_at` calculation with timezone conversion.
- Configurable Vehicle, License Plate, Position, Coordinates, Speed,
  Ignition/Movement and Odometer columns.
- Navirec Status and Last GPS are mandatory safety columns.
- At least one identity column and one position representation are required.
- Missing telemetry renders as Not available; real zero values remain valid.
- HTML escaping is applied to report values.
- HTML report preview and manual Send Now.
- Scheduled report cron runs every five minutes.
- Scheduled reporting triggers a Navirec state refresh before rendering.
- A Navirec refresh failure does not prevent sending last-known/stale data.
- Mail delivery records last attempt, last success and last error.
- Failed scheduled delivery is retried in 30 minutes.
- Successful delivery advances to the next configured local-time slot.

## Configuration

Fleet -> Configuration -> Settings -> Navirec:

1. Enter the API token supplied by the client.
2. Enter the Navirec Account ID when the token sees multiple accounts.
3. Set the Navirec timezone when required.
4. Test Connection.
5. Match Vehicles Now, then run Mapping Audit.
6. Sync All Now and inspect Navirec Sync Logs.
7. Optionally enable Human-readable Navirec locations. The module will prefer
   Navirec Areas/POIs, then nearby event addresses, then nearby trip addresses,
   then Navirec's reverse geocoder.
8. Optionally configure a verified vehicle deep-link template containing
   `{uuid}`.

## Staging / UAT checklist

- Review mapped and not-mapped Fleet filters.
- Confirm the expected active-truck count and investigate every duplicate plate.
- Compare several GPS fixes, timestamps, speed and ignition values with Navirec.
- Confirm a true `0` speed is displayed as zero rather than Not available.
- Confirm stale GPS is visibly marked stale.
- If human-readable locations are enabled, test one vehicle inside a known
  Navirec Area and one vehicle outside Areas but near a recent addressed event.
- Confirm manual **Sync Navirec** keeps/refreshes the same readable location as
  the scheduled sync rather than reverting to coordinates.
- Create an internal tracking subscription with only a small vehicle subset.
- Preview the report and verify no vehicle outside the subscription appears.
- Test all three schedule slots using staging times, then restore production times.
- Verify last-attempt/last-sent/last-error fields and the 30-minute retry path.

## External decisions / sign-off still required

These are not safe to invent in code and are not Phase-1 connector defects:

- Final client recipient and CC addresses.
- The three production send times for each client.
- Whether the Client Agreement requires HTML-only, PDF, XLSX or another
  attachment format. Current delivery is HTML email/report preview.
- The exact Navirec vehicle-specific web route. Configure the deep-link template
  only after verifying it in the client's Navirec web account.
- Whether Navirec's reverse geocoder is enabled for the integration user.
  The module reads `services.geocoding` from `/configuration/` and does not
  call a public geocoding service.
- Final Operations approval of truck/trailer/driver master-data quality.
- Real external email delivery confirmation in a non-neutralized mail environment.

## Deferred phases

Trips, milestone workflows, geofence automation/webhooks, fuel reporting,
real-time NDJSON streaming, automatic Fleet odometer-log creation, embedded maps,
advanced alerts and trip profitability remain Phase 2+ work and are deliberately
outside this Phase 1 module.

## Tests

Run on an Odoo 19 runtime:

```bash
# Use a disposable test database/clone, not the shared UAT database.
# Use -i fleet_navirec instead of -u when the module is not installed yet.
odoo-bin -d <disposable_test_db> -u fleet_navirec --test-tags /fleet_navirec \
  --stop-after-init --no-http --max-cron-threads=0 --log-level=test
```

The test suite covers API headers/pagination/errors, matching, duplicate plates,
state synchronization, missing-vs-zero semantics, integration status, stale data,
Navirec area geometry, nearby event/trip address enrichment, manual-sync parity,
location-cache safety, deep-link safety, monitoring, report
isolation/escaping/configuration, timezone scheduling, delivery success/failure
and retry behavior.


## Readable-location request fixes (19.0.1.2.6)

- Events use `vehicle=<uuid>` for one vehicle or `vehicles=<uuid1>,<uuid2>`
  for a batch. Neither request also sends `account`. Duplicate IDs collapse
  before scope selection; an explicit empty/invalid list is not widened to
  the account. Events pagination is bounded to 20 pages and rejects cycles
  and links to another origin.
- Geocoding configuration always sends both the configured Account ID and
  the owner of the current API token. A UUID `user_id` from that same token
  can supply the routing hint; this does NOT authenticate the token locally.
  Navirec authenticates the unchanged token on every request.
- For tokens without that claim, set **Integration User UUID** in Fleet
  settings to the owner of this integration token. Do not enter an admin's
  UUID or a different integration user's UUID. Conflicting token/setting
  identities are rejected before any request.
- No users list is searched to guess an owner. The client never retries a
  denied configuration call with another user or by dropping account scope.
  A real 403 remains a Navirec permission/configuration issue. Confirm access
  with Navirec; base GPS permission does not establish geocoding permission.
- Test Connection now reports optional Area/geocoding-configuration failures
  separately from a successful base connector check. It does not perform a
  reverse lookup or send a report.
- Reverse requests are restricted to the approved Navirec HTTPS geocoding
  host and never follow redirects with tokens/keys.

### Read-only staging diagnosis

Run in a **fresh Odoo shell** on the `19_upgrade` staging database after the
module upgrade (not in a shell with unsaved work):

```python
exec(open('/home/odoo/src/user/fleet_navirec/scripts/diagnose_readable_location.py').read())
```

The script uses vehicle 39 by default and performs only bounded authenticated
GET requests. It checks one-vehicle and two-vehicle Events filters, resolves
configuration using the token owner, and attempts at most one reverse lookup.
It never prints tokens, geocoding keys, headers, or full configuration payloads;
it sends no email and writes no Odoo records. Set `NAVIREC_DIAG_VEHICLE_ID` in
the shell environment to diagnose a different vehicle. Results from mocked
unit tests are not live Navirec acceptance and are not an Odoo integration-suite
pass. Final UAT still requires both the live result and the Odoo test suite.
