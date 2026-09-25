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
  1 km of the current state. This avoids displaying a readable but stale/wrong
  address from an older event elsewhere.
- A human-readable name is cached while the vehicle remains within 250 meters,
  which avoids unnecessary repeated event lookups for parked vehicles/GPS jitter.
- Manual **Sync Navirec** and scheduled state synchronization use the same
  enrichment path.
- If Areas or event-address enrichment is unavailable, GPS synchronization still
  succeeds and coordinates remain the final fallback.

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
   Navirec Areas/POIs and then a nearby Navirec vehicle-event address.
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
- Whether Navirec Area/POI names plus nearby Navirec event addresses satisfy
  the contractual human-readable-location requirement. External reverse
  geocoding is not used without explicit approval/provider configuration.
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
odoo-bin --test-tags /fleet_navirec --stop-after-init --log-level=test
```

The test suite covers API headers/pagination/errors, matching, duplicate plates,
state synchronization, missing-vs-zero semantics, integration status, stale data,
Navirec area geometry, nearby event-address enrichment, manual-sync parity,
location-cache safety, deep-link safety, monitoring, report
isolation/escaping/configuration, timezone scheduling, delivery success/failure
and retry behavior.
