# Trip Email Updates — Implementation Specification

**Status:** Implemented on `19_upgrade`; static checks pass, Odoo runtime integration tests still required
**Target module:** `taj_transport_operations` with optional Navirec extension in `fleet_navirec`
**Current checkout:** branch `19_upgrade`, `taj_transport_operations` version `19.0.1.18.0`, `fleet_navirec` version `19.0.1.5.1`

> **Version warning:** Although this checkout is under a directory named `odoo15`, the inspected code is an Odoo 19 codebase. This specification targets the checked-out Odoo 19 branch. It must not be applied to an actual Odoo 15 deployment without a separate compatibility review/port.

## 1. Goal and scope

Add an **Email Updates** tab to each transport trip. Dispatchers can configure customer contacts and extra recipients, choose an update interval, preview the position/checkpoint information, send an update immediately, and enable periodic updates while a trip is in progress.

The feature must reuse the existing trip, assignment, milestone, activity, Chatter, and Navirec data. It must not introduce a second trip/milestone workflow or call Navirec synchronously during email generation.

### In scope

- Per-trip recipient configuration and validation.
- Automated periodic email updates for `in_progress` trips.
- Manual update email action.
- HTML email content containing trip header, current/last-known position, and ordered checkpoints.
- Chatter audit for every queued dispatch.
- Mail queue status visibility without claiming inbox delivery.
- Tests for validation, scheduling, rendering, cron filtering, and concurrency.

### Out of scope

- Customer self-service portal or public tracking links.
- Live GPS polling from Navirec during email generation.
- SMS, WhatsApp, attachments, or customer replies.
- Changing trip lifecycle, route generation, milestone event semantics, or Navirec synchronization cadence.

## 2. Architecture and module boundaries

**Do not add `fleet_navirec` to `taj_transport_operations` dependencies.** The current dependency direction is:

```
taj_transport_operations
          ↑
     fleet_navirec
```

Reversing this dependency would create a circular dependency.

### `taj_transport_operations` owns

- Trip email configuration fields and validation.
- Recipient resolution and normalization.
- Scheduling state and lifecycle hooks.
- Email content generation using a provider method.
- Manual send action and periodic cron.
- Dispatch audit model and Chatter note.
- Email Updates tab and base tests.

### `fleet_navirec` owns

- An override of the base position-provider method.
- Reading the already-synchronized `fleet.vehicle` Navirec fields.
- Fresh/stale GPS classification, coordinates, last GPS time, and Google Maps URL.
- Navirec-specific position tests.

The base provider in `taj_transport_operations` must work when `fleet_navirec` is not installed. It returns the latest actual checkpoint event as a fallback. The Navirec extension enriches/overrides that payload when installed.

**No direct Navirec HTTP/API request is allowed from the send action or email cron.** Email rendering must use the locally synchronized vehicle fields so a Navirec outage cannot block mail queueing.

## 3. Data model

Extend `taj.transport.trip` in `taj_transport_operations/models/transport_trip_email.py` (new file, imported from `models/__init__.py`) or the existing trip extension file if the implementation team has a clear reason to avoid a separate file.

### Trip configuration and schedule fields

| Field | Type | Default / behavior |
|---|---|---|
| `email_notification_enabled` | Boolean | False; controls automated updates only |
| `email_partner_ids` | Many2many to `res.partner` | Explicit customer contacts; no automatic follower expansion |
| `email_extra_recipients` | Char | Comma-separated additional email addresses |
| `email_interval_hours` | Float | 4.0; finite positive number, minimum 0.25 hours to match the 15-minute cron resolution |
| `email_next_send_at` | Datetime | Read-only, system-managed, indexed |
| `email_last_queued_at` | Datetime | Read-only, system-managed; replaces ambiguous `email_last_sent_at` |
| `email_dispatch_ids` | One2many | Read-only relation to immutable dispatch audit records |

Use `copy=False` for recipient, enablement, and schedule fields so a newly created trip does not inherit another trip's customer recipients or timer. Use `readonly=True` on system-managed timestamps; protect them against direct RPC/form writes in server-side `write()` validation.

**Timestamp terminology:** the MVP records when messages were queued in Odoo, not when a recipient received them. Therefore use `email_last_queued_at` and label it “Last queued”. Do not display a queued timestamp as “Last sent”. A dispatch can separately show the current `mail.mail` state (Outgoing, Sent by mail server, or Exception). “Sent” means accepted by the configured outgoing mail transport, not confirmed delivery to the recipient's inbox.

### Dispatch audit model

Add `taj.transport.trip.email.dispatch` as an append-only model with at least:

- `trip_id` (required, indexed, ondelete restrict) and related stored `company_id`.
- `dispatch_type`: `manual` or `automatic`.
- `queued_at` (required).
- `requested_by_id` (user; cron may use the configured service user or empty/system marker).
- `recipient_emails` (Text snapshot of normalized recipients at queue time).
- `mail_ids` (Many2many to `mail.mail`, to inspect each individual recipient's mail state).
- A computed aggregate status: `queued`, `sent`, `partial_failure`, or `failed`.

Dispatch rows are audit records: ordinary users must not edit or delete them. Create them through a private server-side method using controlled elevated privileges, while checking trip write access before a manual send. Add read ACL for transport users and company record rules consistent with the existing trip/milestone/activity models. Do not grant ordinary dispatchers create/write/unlink ACLs on the audit model.

### Recipient validation

- `email_partner_ids` may contain only the trip customer or its child contacts: compare each contact's `commercial_partner_id` with the trip customer's `commercial_partner_id`.
- A selected partner must have a valid normalized email address at save time and again at send time.
- Parse `email_extra_recipients` as comma-separated addresses, trim whitespace, normalize using Odoo's `email_normalize`, and reject invalid/empty entries.
- Deduplicate addresses case-insensitively across partner and extra recipients.
- Reject header/control characters and malformed separators; do not accept arbitrary display-name/header fragments.
- Enabling automated updates requires at least one valid recipient.
- A manual send also requires at least one valid recipient, even when automated updates are disabled.
- Do not implicitly include `message_partner_ids`, followers, drivers, or internal employees.

The UI domain is only a convenience. Enforce the customer-contact restriction server-side in constraints and in the send method to prevent RPC bypass.

## 4. Scheduling and lifecycle rules

The cron runs every **15 minutes**, matching the existing transport inactivity cron cadence. It processes only trips satisfying all of:

- `state == 'in_progress'`
- `email_notification_enabled == True`
- `email_next_send_at <= now`

Use a bounded batch size and deterministic ordering (for example, next-send time then trip ID) so a large number of due trips does not monopolize one cron execution.

### Schedule transitions

| Event | Required behavior |
|---|---|
| Enable notifications on Draft/Ready trip | Validate recipients and interval; keep next-send empty until trip starts |
| Trip transitions to `in_progress` with notifications enabled | Set next send to `now + interval` |
| Enable notifications on an already in-progress trip | Set next send to `now + interval` |
| Change interval while enabled and in progress | Recalculate next send as `now + new interval` |
| Disable notifications | Clear next-send timestamp; preserve recipients and audit history |
| Manual send on enabled in-progress trip | Queue immediately, then set next send to `now + interval` |
| Complete or cancel trip | Clear next-send timestamp; automated cron must not send again |
| Manual send on Ready/Completed/Cancelled trip | Allowed as an explicit dispatcher action, provided recipients are valid; do not reactivate automation |
| Manual send on Draft trip | Not allowed; trip must have reached Ready |

Do not automatically disable the user's enablement preference on completion/cancellation; preserve the configuration for audit/history, but clear the timer and let the state filter stop automation.

### Interval calculation and missed runs

After a successful queue operation, calculate:

```
email_next_send_at = queue_time + email_interval_hours
```

Do **not** increment the old scheduled timestamp. If the server was down or the cron was delayed, queue at most one update when it resumes, then start a fresh interval from the actual queue time. This prevents catch-up bursts.

A failed validation or failed mail creation must not advance `email_next_send_at`. Mail creation and schedule advancement must be in the same database transaction.

### Concurrency and duplicate prevention

Manual and automatic sending must share the same per-trip database row lock. Under the lock:

1. Refresh/invalidate the relevant trip fields.
2. Re-check trip state, enablement, recipients, and due time.
3. For cron, skip if another worker/manual action has already moved the next-send time.
4. Create all per-recipient mail queue rows and the dispatch audit record.
5. Update `email_last_queued_at` and (for an enabled in-progress trip) `email_next_send_at`.
6. Post one Chatter audit note.

The queue records, dispatch record, Chatter note, and schedule update must commit or roll back together. This prevents a cron retry from creating a second batch after a partial database failure. A manual action remains an explicit send request; two separate deliberate manual clicks may create two separate dispatches.

Use the trip row lock pattern already present in `taj.transport.trip`; do not change the existing lifecycle transition locking behavior. Cron execution must use a safe elevated environment while preserving company context and record-rule boundaries.

## 5. Email content and position provider

### Email header

Include only customer-safe operational information already present on the trip:

- Trip reference and current state.
- Container number (`container_num`).
- Cargo description/type where available.
- Origin and destination.
- Current/last-known truck position and the time/source of that information.
- Ordered checkpoint table.

Do not include internal Chatter notes, internal-only activity descriptions, costs, accounting data, employee personal details, or internal URLs requiring staff authentication.

### Position provider contract

Add a base method on `taj.transport.trip`, for example `_get_email_update_position_data()`, returning a stable dictionary with:

- `label`: customer-readable location text.
- `source`: `navirec`, `milestone`, or `planned`.
- `updated_at`: timestamp of the GPS fix or actual milestone event, if available.
- `latitude` and `longitude`: optional numeric coordinates.
- `map_url`: optional Google Maps URL.
- `is_stale`: Boolean.
- `notice`: optional explanatory text.

Base implementation (without Navirec installed):
- Find the latest actual Arrival/Departure event for this trip, ordered by `occurred_at desc, id desc`.
- Exclude skipped checkpoints and planned-only checkpoint coordinates.
- Use the checkpoint name and actual event time; label it “Last recorded checkpoint”, not “current GPS position”.
- If there is no actual event, return the planned origin/location with source `planned` and an explicit “planned location; not a live GPS fix” notice.
- Never present a planned checkpoint's latitude/longitude as the truck's actual current coordinates.

Navirec override in `fleet_navirec`:
- Read the assigned truck from the trip's current assignment/current vehicle relationship.
- Reuse synchronized fields: `navirec_has_position`, `navirec_last_lat`, `navirec_last_lon`, `navirec_position_display`, `navirec_last_state_time`, and `navirec_is_stale`.
- When GPS is fresh, return the readable location, coordinates, timestamp, and a Google Maps link.
- When GPS is stale, do not label it as current. Prefer the latest actual milestone as the main fallback and optionally include the stale GPS fix as a clearly marked “last GPS fix” detail.
- When no GPS fix exists, use the base milestone/planned fallback.
- Do not call Navirec API or geocoding endpoints during email rendering.

### Checkpoint table

Render `milestone_ids` in the model's existing sequence order (`sequence, id`). For each checkpoint include:

- Checkpoint name.
- Planned arrival time (`planned_arrival_at`).
- Actual arrival time (`actual_arrival_at`).
- Actual departure time (`actual_departure_at`).
- State: Pending, Arrived, Departed, or Skipped.

Use the model's existing state semantics; do not infer a departure from a planned time. Format datetimes in the trip/company timezone for display and retain an unambiguous timezone indication in the email footer.

### HTML safety and recipients' privacy

Build the HTML with escaped values (QWeb escaping or `markupsafe` helpers consistent with existing project patterns). Never interpolate untrusted cargo, location, partner, or checkpoint text as raw HTML.

Send **one mail per unique recipient address** using the same rendered body. Do not place all customer addresses in a shared `To` field where recipients can see one another. Keep mail records (`auto_delete=False`) so the dispatch audit can show per-recipient queue state. Use the configured outgoing mail server/company sender; never trust a sender address supplied in the extra-recipient field.

## 6. UI and actions

Add an **Email Updates** page to the existing trip form notebook in `views/transport_trip_views.xml`.

### Settings group

- Enable automated updates toggle.
- Customer contact tags widget, domain-limited to the trip customer hierarchy.
- Additional recipient addresses (comma-separated).
- Interval in hours (default 4.0; validation feedback for values below 0.25 or invalid numbers).

### Schedule and preview group

- Next scheduled update (read-only).
- Last queued update (read-only; do not label it “sent”).
- Current/last-known position preview, source, timestamp, stale notice, and clickable Google Maps link when coordinates exist.
- A clear message when the preview is a planned location or a last recorded checkpoint rather than live GPS.

### Manual action

Add **Send Update Email Now** to the Email Updates tab. The action:
- Requires trip write access and at least Ready state.
- Revalidates all recipients and builds a fresh position/checkpoint snapshot.
- Queues one message per unique recipient and logs one dispatch.
- Works even if periodic notifications are disabled.
- If the trip is In Progress and automation is enabled, resets the next-send time to `now + interval`.
- Shows a success notification only after the transaction is successfully queued; it must not claim that the email has reached the recipient.

The action must be protected server-side; hiding/disabling the button in XML is not a security boundary.

## 7. Cron and mail implementation

Add a cron record in `taj_transport_operations/data/trip_email_cron.xml`:

- Model: `taj.transport.trip`.
- Method: `_cron_send_due_trip_email_updates()`.
- Interval: 15 minutes.
- Active by default.
- Add the file to the module manifest.

The cron should search due trips in a bounded batch, lock each trip, re-check the due domain after locking, and call the same internal dispatch method used by the manual action. It must continue processing other trips if one trip fails validation or mail creation; log the exception with trip reference and do not advance that trip's timer.

Implement a private method such as `_queue_trip_email_update(dispatch_type)` as the single source of truth for recipient resolution, HTML rendering, mail creation, dispatch audit, Chatter logging, and timer updates. Do not duplicate the send logic between cron and button.

Use the existing inactivity-alert implementation as a style/reference for `mail.mail` creation and Chatter conventions, but do not reuse its company-wide recipient setting or digest behavior.

## 8. Chatter audit and mail status

For each manual or automatic dispatch, post exactly one internal Chatter note containing:

- Dispatch type (Manual / Automatic).
- Queue timestamp.
- Normalized recipient list snapshot.
- Dispatch audit record reference.
- A neutral status phrase such as “Email update queued”; do not say “delivered”.

The dispatch audit record links the individual `mail.mail` rows. Its aggregate state is:
- `queued`: at least one mail is still outgoing/processing.
- `sent`: all recipient mail rows are in Odoo's sent state.
- `partial_failure`: a mix of sent and exception rows.
- `failed`: all recipient mail rows failed.

This state reflects Odoo's mail transport status only; it cannot prove inbox delivery, spam-folder placement, or that a person read the message.

Do not expose the full recipient list in a customer-visible message or public chatter subtype. Use an internal note.

## 9. Security and access control

- Reuse the existing transport trip access model and company scoping.
- Add company record rules and read-only ACLs for the dispatch audit model.
- Do not give ordinary users direct create/write/unlink access to audit rows.
- Validate customer-contact ownership on the server for both save and send.
- Validate extra email addresses on save and again at send time.
- Protect system-managed schedule fields from direct user/RPC writes.
- Use a controlled `sudo()` only for creating mail queue/audit records after the caller's trip access and business validations have passed.
- Preserve `company_id` context for mail rendering and queue creation.
- Avoid logging credentials, Navirec tokens, or sensitive API payloads.

## 10. Tests and acceptance criteria

Add tests under `taj_transport_operations/tests` and Navirec-specific tests under `fleet_navirec/tests`.

### Recipient validation

- Valid customer parent and child contacts are accepted.
- A partner belonging to another commercial customer is rejected.
- Partner without a valid email is rejected.
- Invalid extra address is rejected.
- Duplicate addresses across partners/extras are deduplicated.
- Multiple comma-separated addresses are parsed correctly.
- Enabling with no valid recipient is rejected.
- Invalid recipients introduced after configuration are caught again at send time.

### Scheduling

- Enabling on Draft/Ready does not start the timer.
- Starting an enabled trip sets next-send to now plus interval.
- Enabling an already in-progress trip sets next-send correctly.
- Changing interval resets the next-send time.
- Disabling clears next-send.
- Manual send on enabled in-progress trip resets next-send.
- Completing/cancelling clears next-send and prevents cron sending.
- Delayed cron queues once and schedules from actual queue time (no catch-up burst).
- Failed mail creation does not advance the timer.

### Sending, audit, and concurrency

- Manual send queues one mail per unique recipient.
- Each recipient receives a separate mail row; recipient addresses are not exposed to one another.
- One dispatch audit record and one internal Chatter note are created per dispatch.
- Mail rows remain available for status inspection.
- Cron ignores Draft, Ready, Completed, and Cancelled trips.
- Cron ignores disabled or not-yet-due trips.
- Cron sends due In Progress trips.
- Concurrent cron runs/manual send on the same trip do not produce a cron/manual duplicate for the same due window.
- A failure on one trip does not prevent later due trips from being processed.
- Unauthorized users cannot send or alter recipient/schedule data through RPC.

### Rendering and position

- Header contains the trip reference, state, container, cargo, origin, and destination where available.
- Milestones are rendered in sequence order with planned/actual times and state.
- User-controlled strings are HTML-escaped.
- No internal Chatter notes or private operational fields appear in the customer email.
- Base module renders the latest actual milestone fallback without Navirec installed.
- Navirec extension renders fresh readable GPS, coordinates, timestamp, and map URL.
- Stale GPS is never labelled current; fallback wording is explicit.
- Missing GPS and missing milestone data render a safe “position unavailable/planned” message.
- Timezone formatting is correct.

### Definition of done

- All new tests pass on the target Odoo 19 test database.
- Existing trip lifecycle, milestone, activity, and Navirec tests continue to pass.
- No circular module dependency is introduced.
- No direct Navirec API call occurs during mail rendering or cron sending.
- Staging UAT verifies manual send, periodic send, disable/complete behavior, stale GPS fallback, and per-recipient privacy.
- Production deployment happens only after staging acceptance.

## 11. Files expected to change

### `taj_transport_operations`

- `models/transport_trip_email.py` (new): fields, validation, provider fallback, queue/send methods, cron method.
- `models/transport_trip_email_dispatch.py` (new): append-only dispatch audit model and mail-state aggregation.
- `models/__init__.py`: import new model files.
- `data/trip_email_cron.xml` (new): 15-minute cron.
- `views/transport_trip_views.xml`: Email Updates tab and manual action.
- `security/ir.model.access.csv`: read access for dispatch audit.
- `security/transport_security.xml`: company record rule for dispatch audit.
- `__manifest__.py`: include cron data and bump module version to `19.0.1.18.0` when implementation is ready.
- `tests/test_trip_email_updates.py` (new): core tests.

### `fleet_navirec`

- New model extension file (for example `models/transport_trip_email.py`) overriding the base position provider.
- `models/__init__.py`: import the extension.
- `tests/test_trip_email_position.py` (new): fresh/stale/missing GPS and map-link tests.
- Bump its module version only if its code changes.

## 12. Implementation order

**Phase A — Core model and validation:** fields, contact ownership, email normalization, schedule protection, base milestone position provider.

**Phase B — Dispatch and audit:** dispatch model, safe HTML rendering, one-mail-per-recipient queue, Chatter audit, aggregate mail state.

**Phase C — Scheduling:** lifecycle integration, manual action, 15-minute cron, row locking, failure handling.

**Phase D — Navirec extension:** GPS provider override, stale handling, map URL, fallback tests.

**Phase E — UI and UAT:** Email Updates tab, preview, manual action, test suite, staging validation.

Do not begin production deployment until all phases and staging acceptance criteria are complete.
