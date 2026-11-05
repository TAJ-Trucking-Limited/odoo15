# TAJ Navirec Phase 1 Completion Plan

**Project:** TAJ Odoo 19 Upgrade / Staging  
**Branch:** `19_upgrade`  
**Module:** `fleet_navirec`  
**Status:** Phase 1 connector foundation is running on staging; Phase 1 is not yet complete.  
**Source of truth:** `TAJ_Tracking_Internal_Technical_Spec_v1.docx`, especially Sections 4, 11, 13, 15, 17 and 18.

## Implementation Progress — 2026-09-25

Implemented locally on branch `19_upgrade` and ready for staging upgrade validation:

- Dedicated **Navirec / Telematics** Fleet tab with integration status, stale warnings,
  current position, movement, ignition, speed, odometer and unavailable-value handling.
- Fleet mapping audit filters for Navirec mapped / not mapped vehicles.
- Persisted Navirec sync history plus last-success / last-error monitoring in Fleet Settings.
- **Sync All Now**, **Match Vehicles Now** and **View Sync Logs** admin actions.
- Per-client **Client Tracking Report** subscription model with explicit vehicle selection.
- Configurable report columns with mandatory freshness/safety columns.
- HTML Fleet Position Report preview and manual send.
- Three-times-daily, per-client timezone-aware scheduling.
- Scheduled pre-report Navirec refresh and stale-data fallback.
- Delivery error logging plus 30-minute retry for scheduled failures.
- Tests added for telematics status, missing-vs-zero values, monitoring, client isolation,
  schedule calculation, configurable columns, preview and delivery failure handling.

Still requires external/runtime confirmation before Phase 1 sign-off:

- Odoo 19 module upgrade/runtime validation on staging.
- Exact client report attachment/output format from the Client Agreement.
- Human-readable location source: Navirec area/POI payload or an explicitly approved
  reverse-geocoding provider.
- Exact stable Navirec vehicle-detail deep-link format; the current button opens Navirec.
- Operations review of the 45-truck mapping and final client recipients/send times.

## 1. Phase 1 Definition

The technical specification defines Phase 1 as **Fleet Foundation + Client Tracking Report**.

Phase 1 must deliver:

1. Clean/configured Odoo Fleet records for trucks, trailers and drivers.
2. Navirec connector for current vehicle position synchronization.
3. Per-client filtered tracking report.
4. Configurable report fields.
5. Scheduled client delivery, three times per day.
6. Safe stale-data handling and basic integration monitoring.

Anything related to Transport Operations trips, milestones, geofence automation,
advanced reporting, profitability or trip financials remains outside Phase 1.

## 2. Current Staging State

Already implemented in `fleet_navirec`:

- API-token authentication and pinned Navirec API version.
- Vehicle matching by normalized license plate.
- Current GPS latitude/longitude synchronization.
- GPS timestamp, speed, ignition and odometer synchronization.
- Manual **Sync Navirec** action.
- **View on Map** action.
- **Open Navirec** action.
- Five-minute state synchronization cron.
- Daily vehicle matching cron.
- Stale-data calculation and warning support.
- API error handling, pagination, retry-once for HTTP 429.
- Unit tests and sanitized fixture structure.

The staging screenshots confirm that mapped trucks are receiving current Navirec data.

## 3. Remaining Gaps Before Phase 1 Sign-Off

The core Phase 1 code is now implemented locally. The remaining gaps are runtime,
data-quality and externally defined requirements rather than missing report/schedule code.

Remaining items:

- Upgrade and validate the module on Odoo 19 staging.
- Audit the 45-truck mapping and clean Fleet/trailer/driver master data with Operations.
- Confirm the final client recipients and the three production send times.
- Confirm the required final attachment/output format from the Client Agreement
  (HTML preview/body is implemented; PDF/XLSX is not assumed without confirmation).
- Confirm an approved source for human-readable location names. The current Navirec
  state payload used by TAJ provides coordinates; navirec_location_name is prepared
  in the model but is intentionally not fabricated.
- Confirm Navirec's stable vehicle-detail deep-link format. The current action opens
  Navirec safely at application level.
- Validate real scheduled email delivery/retry behavior on staging mail infrastructure.
- Obtain Operations acceptance of the final report content and Fleet workflow.

## 4. Architecture Decisions
### 4.1 Keep Phase 1 inside `fleet_navirec`

The connector, tracking configuration, position report and schedule belong to the
same Phase 1 business capability. Keep them in `fleet_navirec` for now.

Do **not** make `fleet_navirec` depend on `send_report_via_mail`.

Reason: `send_report_via_mail` currently depends on accounting modules,
`move_invoice_line` and `report_xlsx`. Pulling that dependency into Fleet would
unnecessarily couple the Navirec connector to accounting.

We can reuse its proven mail pattern (`mail.mail`, attachments, cron design)
without importing the module.

Add `mail` to `fleet_navirec` dependencies when the report delivery work starts.

### 4.2 Explicit client-to-vehicle assignment

Do not infer report ownership from license plate, driver, sale order or current trip.

Create an explicit tracking subscription/configuration that states:

- which client receives the report;
- which Fleet vehicles belong in that client's report;
- who receives the email;
- which fields are included;
- what timezone/schedule is used.

This prevents accidental cross-client vehicle disclosure.

### 4.3 Current-state reporting only

Phase 1 report rows are generated from the latest synchronized values stored on
`fleet.vehicle`. Do not duplicate raw GPS breadcrumb history into Odoo.

## 5. Workstream A — Finish Fleet / Telematics UX
### A1. Add a dedicated Navirec / Telematics tab

Refactor `views/fleet_vehicle_views.xml` so each mapped truck has a dedicated
**Navirec / Telematics** page instead of a loose group at the bottom of the form.

Show:

- integration status badge;
- Navirec vehicle UUID;
- current position / location;
- latitude and longitude;
- last GPS communication time;
- speed;
- ignition / movement state;
- synchronized odometer when available;
- stale warning when applicable;
- buttons for Sync, Map and Open Navirec.

### A2. Add integration status

Add a computed status on `fleet.vehicle` with clear states:

- `not_mapped` — no Navirec UUID;
- `awaiting_data` — mapped but no GPS state received yet;
- `connected` — mapped and GPS timestamp is fresh;
- `stale` — mapped but last GPS timestamp exceeds configured threshold.

Display it as a badge in the Telematics tab and optionally in the Fleet list view.

### A3. Do not show missing values as real zero values

Add explicit availability flags where Odoo Float fields would otherwise render
missing data as `0.00`.

At minimum:
- `navirec_has_position` (already present);
- `navirec_has_odometer`;
- future optional flags for fuel/CAN fields if they enter Phase 1.

UI should display **Not available** when Navirec did not send the value.

A real value of zero must remain valid.

### A4. Human-readable current position

Add `navirec_location_name` to `fleet.vehicle`.

Implementation order:

1. Check whether the live Navirec account exposes a usable area/POI name for the
   current coordinate through the approved API surface.
2. If yes, use Navirec area/POI naming as the preferred Phase 1 location label.
3. If no usable Navirec location name exists, keep coordinates + Google Maps and
   record this as a known spec dependency.
4. Do not introduce an external reverse-geocoding provider without explicit approval.

The state endpoint already validated for TAJ does not directly provide a postal
address, so this item requires a focused API check before implementation.

### A5. Open exact Navirec vehicle when possible

Verify the Navirec web application's stable vehicle-detail URL format.

If a stable UUID-based deep link exists, update `action_open_in_navirec()`.
Otherwise retain the application-level link and document the limitation.

## 6. Workstream B — Integration Monitoring
### B1. Persist synchronization summary

Operations needs more than server log lines.

Expose at least:

- last sync attempt;
- last successful vehicle-state sync;
- last successful vehicle-matching sync;
- last error time;
- last error summary;
- number of states fetched/updated on the last successful run.

A lightweight persisted summary can live in `ir.config_parameter` initially.

### B2. Add synchronization history

Add a small model such as `fleet.navirec.sync.log` if staging review confirms
Operations needs searchable history.

Suggested fields:

- operation (`vehicle_match` / `state_sync`);
- started_at;
- finished_at;
- status (`success` / `partial` / `error`);
- records_received;
- records_updated;
- message/error summary.

The cron should still swallow connector exceptions after logging them so a Navirec
outage does not break unrelated Odoo workflows.

### B3. Settings status panel

Extend Fleet > Configuration > Settings > Navirec with:

- Test Connection;
- last successful state sync;
- last successful vehicle match;
- last error;
- stale threshold.
## 7. Workstream C — Per-Client Tracking Configuration

Create a new model, proposed name:

`fleet.navirec.report.subscription`

Purpose: one record describes one client's tracking report subscription.

### Required fields

- `name`
- `active`
- `partner_id` — client/customer
- `vehicle_ids` — explicit Many2many to `fleet.vehicle`
- `email_to`
- `email_cc`
- `timezone`
- three configurable daily send times
- `next_send_at`
- `last_sent_at`
- `report_format` if the Client Agreement requires format choice

### Configurable report fields

Start with explicit booleans so behavior is predictable:

- vehicle/truck name;
- license plate;
- integration status;
- human-readable current position;
- latitude/longitude;
- last GPS timestamp;
- speed;
- ignition/movement;
- odometer when available.

The exact default column set must be checked against the Client Agreement before
final sign-off.
### Security

Only Fleet/Operations managers should configure subscriptions.

Recipients must never be able to cause another client's vehicles to appear in
their report. The report domain must always start from the subscription's
explicit `vehicle_ids`.

## 8. Workstream D — Fleet Position Report

### D1. Report data builder

Create a single tested method that produces normalized report rows from the
subscription's vehicles.

Each row should carry display-ready values, not raw API payloads.

Minimum row contract:

- truck;
- plate;
- current position/location;
- coordinates if enabled;
- last GPS update;
- freshness/status;
- speed/movement if enabled;
- odometer if available/enabled.

### D2. Report presentation

Phase 1 should support the format required by the Client Agreement.

Until that document is confirmed, implement the report generator so output
formatting is separated from row preparation.

Recommended structure:

- HTML table for the email body;
- attachment renderer kept separate so XLSX/PDF can be added without changing
  filtering/business logic.
### D3. Report freshness rule

Before a scheduled report is generated:

1. trigger or verify a recent Navirec state refresh;
2. generate the report from the newly stored Fleet values;
3. visibly mark stale vehicles;
4. never label an old GPS position as current/live.

A Navirec outage must not block the email entirely unless there is no usable
report data. The report should show the last known timestamp and stale state.

## 9. Workstream E — Three-Times-Daily Delivery

Use one scheduler that checks due subscriptions rather than creating three global
cron records per client.

Recommended approach:

- cron runs every 5–10 minutes;
- each subscription stores three local send times and a timezone;
- `next_send_at` is stored in UTC;
- cron selects active subscriptions where `next_send_at <= now`;
- report is generated and sent;
- `last_sent_at` is updated;
- next configured slot is calculated and stored.

This supports different schedules per client while still satisfying the
three-times-daily requirement.

Use standard Odoo `mail.mail` / mail infrastructure.

Do not reuse the hard-coded aged-report recipients from
`send_report_via_mail`.

## 10. Workstream F — Fleet Foundation Data Audit
Phase 1 also includes Fleet foundation/cleanup, so staging needs a data audit in
parallel with code work.

Checklist:

- every active TAJ truck exists once in Odoo Fleet;
- every active Navirec truck has a clean plate match candidate;
- normalized Odoo plates contain no duplicates;
- normalized Navirec plates contain no duplicates;
- matched vehicles have the expected Navirec UUID;
- unmapped vehicles are listed for Operations to correct;
- trailer master data is reviewed;
- driver master data is reviewed;
- inactive/retired vehicles are clearly identified.

Produce a simple mapping/audit result for Operations before Phase 1 sign-off.

## 11. Tests Required Before Phase 1 Sign-Off

### Connector tests

Keep the existing API/sync tests and add coverage for:

- integration status states;
- odometer unavailable vs true zero;
- stale threshold behavior;
- location-name population/fallback;
- sync summary/log updates.

### Tracking subscription tests

Add tests for:

- explicit client vehicle filtering;
- no cross-client leakage;
- configurable fields;
- inactive subscription ignored;
- missing recipients rejected cleanly;
- timezone and next-send calculation.
### Report tests

Add tests for:

- fresh vehicle row;
- stale vehicle row;
- missing odometer displayed as unavailable;
- true zero coordinate remains valid;
- report only contains subscription vehicles;
- enabled/disabled columns;
- HTML escaping of vehicle/client names.

### Scheduler/email tests

Add tests for:

- due subscription sends once;
- not-due subscription does not send;
- next send time advances correctly;
- failed Navirec refresh still produces a stale-marked report when last-known data exists;
- mail failure is logged and does not mark the subscription as successfully sent.

## 12. Staging Validation Checklist

After each implementation commit is deployed to `19_upgrade` staging:

1. Upgrade `fleet_navirec`.
2. Open several mapped and unmapped Fleet vehicles.
3. Verify Telematics status, stale behavior and unavailable-value display.
4. Run vehicle matching manually and review unmatched/duplicate logs.
5. Run state sync manually and verify actual Navirec values.
6. Configure one internal test tracking subscription.
7. Assign a small subset of trucks.
8. Generate the report manually and compare rows with Fleet records.
9. Verify no vehicle outside the subscription appears.
10. Send the report to internal test recipients only.
11. Validate all three scheduled send slots using shortened staging times.
12. Restore real staging schedule after validation.
## 13. Implementation Order / Commit Plan

### Commit 1 — Fleet telematics UX hardening

- dedicated Navirec / Telematics tab;
- integration status;
- unavailable-value handling;
- stale UI cleanup;
- deep-link verification;
- related tests.

### Commit 2 — Sync monitoring

- persisted last-success/last-error summary;
- optional sync history model;
- Settings status panel;
- tests.

### Commit 3 — Client tracking subscriptions

- configuration model;
- security/access;
- views/menu;
- vehicle assignment;
- recipient/schedule/field configuration;
- tests.

### Commit 4 — Fleet Position Report

- normalized report-row builder;
- HTML presentation;
- required attachment renderer after Client Agreement format confirmation;
- manual Generate/Preview/Send actions;
- tests.

### Commit 5 — Scheduled delivery

- three-times-daily per-subscription scheduling;
- pre-report Navirec refresh behavior;
- mail sending;
- failure logging/retry-safe behavior;
- tests.
### Commit 6 — Staging hardening and Operations sign-off

- full 45-truck mapping audit;
- stale/offline scenario;
- bad-token/API-outage scenario;
- client isolation test;
- schedule verification;
- Operations feedback fixes.

## 14. Acceptance Criteria for Phase 1

Phase 1 is complete only when all of the following are true:

- Odoo Fleet records required for Phase 1 are clean enough for reliable mapping.
- Active Navirec trucks map deterministically to Odoo vehicles.
- Current Navirec position and GPS timestamp synchronize automatically.
- Fleet users can clearly see mapped/connected/stale/unmapped state.
- Old GPS data is never presented as live.
- Missing telemetry is distinguishable from real zero values.
- Operations can see the last successful sync and useful failure information.
- A client-specific tracking subscription can select its own vehicles.
- A client report contains only those selected vehicles.
- Report fields are configurable.
- The report visibly includes freshness/last-GPS information.
- The report can be generated manually for validation.
- The report is sent automatically three times per day using the configured timezone/schedule.
- Delivery failures are logged and do not silently count as successful sends.
- Tests cover filtering, stale data, scheduling and failure paths.
- Staging Operations users approve the workflow and report output.

## 15. Open Decisions Before Final Report Formatting
These items are not fully defined by the internal technical specification and
must be confirmed from the Client Agreement or by TAJ/Prime Shift before final
report sign-off:

1. Exact default report columns and their order.
2. Required attachment format: XLSX, PDF, HTML-only, or a combination.
3. Exact three daily send times for each client.
4. Primary and CC recipients for each client.
5. Whether a Navirec area/POI name is sufficient as the human-readable location
   or an approved reverse-geocoding service is required.
6. Whether Phase 1 must expose fuel/CAN values where available, or whether those
   remain deferred telemetry enhancements.

These open items should not block implementation of the subscription model,
filtering, scheduling engine, status UI or report-row builder.

## 16. Immediate Next Step

Start with **Commit 1 — Fleet telematics UX hardening** on `19_upgrade`.

After that is on staging, implement the client tracking subscription model before
building the report renderer. This keeps filtering/security correct before any
email can be generated or sent.
