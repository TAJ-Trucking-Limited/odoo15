# TAJ Transport: ready-to-use manual UAT data

Use this data pack with [the full manual UAT checklist](TRANSPORT_FULL_UAT.md).
The setup script creates new staging fixtures; it does not execute or pass any
acceptance cases. It has not been run against your staging database by the agent.

The script is [scripts/taj_transport_prepare_uat.py](../scripts/taj_transport_prepare_uat.py).
It is locked to staging database `taj-limited-19-upgrade-38909886` and company
`TAJ Trucking Limited`. After an Odoo.sh staging rebuild changes the database
name, review and update its EXPECTED_DATABASE constant before using it there.

## Before loading

1. Confirm the staging build has this script. Upgrade Transport Operations to
   `19.0.1.11.0` and Navirec Fleet Integration to `19.0.1.4.0` or later compatible
   versions if needed. A successful Git push alone is not a module upgrade.
2. Keep a staging backup. This pack only creates new records with its UAT prefix;
   it does not clear your current trips, assignments or operational data.
3. The company needs its existing chart/accounts and sales journal. Exactly one
   transporter named `TAJ` must exist for default selection testing. Existing
   taxes, accounts, users, categories, settings and transporter data are reused
   or read, not renamed or reconfigured.
4. Use the default run prefix `UAT-<today's date>-01`, or choose a new one.
   The script refuses a used prefix instead of overwriting work or adding a
   second copy. Failed creation rolls back the entire run.

## Load from the Odoo.sh terminal

These commands require the script to be present in the staging checkout at
`~/src/user/scripts/taj_transport_prepare_uat.py`.
The self-contained paste command supplied with this data pack embeds the same
script and works without pushing files to the staging checkout first. Its first
line uses `TAJ_UAT_APPLY=YES` for creation; change that to `TAJ_UAT_APPLY=NO`
to preview only, then use YES when ready to create.

First preview the company, currency, tax choice and proposed records:

```bash
odoo-bin shell -d "$PGDATABASE" --no-http --max-cron-threads=0 \
  < ~/src/user/scripts/taj_transport_prepare_uat.py
```

This preview writes nothing. The tax is an existing active, tax-excluded,
percentage sales tax in the company/tax country. The default is the first
eligible tax by ID, **not a recommendation of a legal VAT rate**. If none exists,
the pack uses 0% and reports that VAT-specific acceptance remains uncovered.
To pick a particular eligible existing tax, include `TAJ_UAT_TAX_ID=<ID>` in
both preview and create commands. No new tax is created.

After checking the preview, create the records:

```bash
TAJ_UAT_APPLY=YES odoo-bin shell -d "$PGDATABASE" --no-http --max-cron-threads=0 \
  < ~/src/user/scripts/taj_transport_prepare_uat.py
```

Copy the JSON manifest printed after **SAVED UAT DATA** into your UAT notes.
It contains the actual IDs, sales order numbers/references, totals, account/tax
choice and sample timestamps for your database. If an error appears, the
script rolls back; do not assume a partial dataset was saved.

For a new independent dataset later, choose a fresh prefix in both commands:

```bash
TAJ_UAT_PREFIX=UAT-20261023-02 odoo-bin shell -d "$PGDATABASE" --no-http --max-cron-threads=0 \
  < ~/src/user/scripts/taj_transport_prepare_uat.py
```

Add `TAJ_UAT_APPLY=YES` only to the creation command. Use your desired date/run
suffix; it is a label, not a fixed appointment date.

## What you get

Every new record carries the chosen prefix. The table below omits it for
readability: Truck A's actual plate is `<prefix>-TRUCK-A`, for example.

| Data | Contents |
|---|---|
| Customers | Customer A and Customer B, in TAJ's company, without real email/phone details. |
| Driver Contacts | Driver 1, Driver 2, Driver 3, in the same company. |
| Test users | A normal Transport Dispatcher and Transport Manager with Sales access to all documents in TAJ's company. Neither has Settings/Fleet maintenance rights. No password is generated or invitation emailed. |
| Configuration | Carrier with optional email empty, General Cargo type, Loading/Border/Delivery checkpoint types. Existing TAJ transporter stays unchanged. |
| Fleet | Six active trucks, two active trailers, one archived truck, one vehicle of another category. |
| Products | An unambiguous route service, a plain freight service and an ambiguous two-separator route service. Ordered-quantity invoicing; no automatic task/project service tracking. |
| Commercial setup | New UAT pricelist in the company currency, new 30-day terms, existing company income account and eligible tax. |
| Routes | Route A with checkpoints at 0/12/24 hours; Route B at 0/6 hours. |
| Sales | Twelve quotations, no confirmed orders yet. No trips, resource assignments or invoices created. |
| Reports | Two inactive Client Tracking subscriptions, one per customer. Recipient is `uat-review@example.invalid`; no delivery is attempted. New trip columns remain off for you to enable/test. |

These prepopulated records are fixtures, not evidence that their UI creation
cases passed. For checklist cases that explicitly create a catalog, route or
report, create another uniquely named item such as `<prefix> UI Carrier` or
`<prefix> UI Route`. Keep Route A/B and the main vehicles available for their
designated journey, and record additional manually created IDs in your notes.

### Vehicles and expected initial availability

All active new vehicles should initially be **Standby** because the script
does not create any trips. The archived truck is found through Archived.
Draft *quotations* containing vehicle references do not reserve vehicles.

| Vehicle | Fleet default driver | Intended use |
|---|---|---|
| TRUCK-A | Driver 1 | MAIN's original and initial assigned truck. |
| TRUCK-B | Driver 2 | MAIN's replacement truck. No seeded quotation reserves it. |
| TRUCK-C | Empty | PLAN and MIXED. Tests a truck without a default driver. Finish auxiliary trips before reusing it. |
| TRUCK-D | Driver 3 | Cancellation and other auxiliary cases, one active case at a time. |
| TRUCK-E | Driver 1 | REPORT-A. |
| TRUCK-F | Driver 2 | REPORT-B. Keeps Customer B separate from A's main journey. |
| TRAILER-A / TRAILER-B | Empty | MAIN's initial/replacement trailer. |
| ARCHIVED-TRUCK | Empty | Archived-resource selection rejection. |
| OTHER-CAR | Empty | Included in availability, excluded from Truck/Trailer assignment choices. |

The Fleet model's technical vehicle type may display Car; Truck/Trailer resource
eligibility is controlled by the module's **category**, not that technical type.
There are no real plates, Navirec UUIDs or invented GPS readings in this pack.

### Quotations and deliberate gaps

Find orders through Sales using **Customer Reference** starting with your
prefix. The manifest maps every reference to its actual sales order number.

| Reference suffix | Customer | Lines and vehicle | Expected untaxed amount | Purpose |
|---|---|---|---:|---|
| MAIN | A | Route service, qty 3 × 1,000, Truck A | 3,000 | Full journey, source-data/invoice checks, replacement A → B. |
| PLAN | A | Plain service, qty 1 × 1,000, Truck C | 1,000 | Route selection, switching, manual checkpoints and regeneration. |
| NO-VEHICLE | A | Two plain lines: 1,000 and 1,250; no vehicles | 2,250 | Missing-truck warning; no trip can be created. |
| MIXED | A | Truck C line 1,000; truckless line 1,250; section and note | 2,250 | Only the trucked line is offered; section/note exclusion. |
| FILL-EMPTY | A | Plain service, 1,000, no vehicle | 1,000 | Empty cargo metadata, source document and terms; wizard and counters. |
| AMBIGUOUS | A | Ambiguous route product, 1,000, no vehicle | 1,000 | No guessed origin/destination from an ambiguous product name. |
| QUOTE-ONLY | A | Plain service, 1,000, no vehicle | 1,000 | Confirm the header Create Trips action is absent before confirmation. |
| CANCEL-D | A | Plain service, 1,000, Truck D | 1,000 | Cancel in Draft. |
| CANCEL-R | A | Plain service, 1,000, Truck D | 1,000 | Plan/assign, mark Ready, then cancel. |
| CANCEL-P | A | Plain service, 1,000, Truck D | 1,000 | Start, compare Dispatcher/Manager cancellation permissions. |
| REPORT-A | A | Route service, 1,000, Truck E | 1,000 | Client A's current trip in tracking reports. |
| REPORT-B | B | Route service, 1,000, Truck F | 1,000 | Another customer's data and report isolation. |

Most lines contain `UAT-CONT-001`, `40ft`, `24000 kg`, a prefixed cargo file,
shipment reference and consignee. FILL-EMPTY intentionally omits those fields;
NO-VEHICLE also has an incomplete line. Quantity, unit price and currency are
still valid, rather than missing or invalid data disguised as a negative case.

Totals include the actual chosen tax and are printed in the manifest. For MAIN,
an ordinary single 18% tax would give 3,000 untaxed + 540 tax = 3,540 total;
use that example **only if the preview actually selected 18%**. The manifest's
actual totals and tax ID are the comparison baseline.

## Start the manual run

1. In Settings > Users find the two prefixed UAT users. Use the normal change
   password action to set private staging passwords yourself. No shared/default
   password is printed by the script. Sign in in separate browser profiles.
2. Confirm MAIN yourself. Verify confirmation creates no trip. Then use Create
   Trips / the line Trip button to create its trip. Record the generated trip ID.
3. Check TAJ defaults, the Truck A snapshot, source quantity 3, unit price 1,000,
   source cargo details and route-name suggestions.
4. Select Route A. Use the manifest's `sample_times_local` for the MAIN timeline;
   both new test users use `Africa/Dar_es_Salaam` so those displayed times match.
   Initial resources: Truck A, Trailer A, Driver 1. Do not put an Actual Arrival
   on the trip before Start.
5. Mark Ready, Start, record checkpoints in order, replace with Truck B/Trailer
   B, then Complete and upload POD. Compare source/invoice data as the full guide
   describes. A quantity of 3 must still produce only one trip for MAIN's line.
6. Use each auxiliary order only for its designated case. Run CANCEL-D, CANCEL-R
   and CANCEL-P sequentially and finish each before starting the next on Truck D.
7. For Client Tracking Reports use an existing Fleet Manager account. UAT
   Transport Manager does not automatically get Fleet Manager rights. Preview
   and download before testing delivery. Keep subscriptions inactive; replace
   the `.invalid` recipient with a tester-owned mailbox only for delivery cases.

Sample upload documents: [original POD](uat-data/pod-original.txt) and
[replacement POD](uat-data/pod-replacement.txt). These text documents exercise
file upload, persistence, filename and download. Use a test PDF/image too if
acceptance specifically needs those formats. The module stores one POD file.

## Optional second-company data

Customer B above is a different customer **inside TAJ's company**, not a second
company. That alone cannot prove company isolation.

If another staging company already exists, include
`TAJ_UAT_SECOND_COMPANY_ID=<existing-company-ID>` in both commands. This adds a
customer, driver, truck, route, service product, pricelist and quotation in that
company. It does not create a company or grant existing users extra access.
An administrator allowed in that company must confirm/create the foreign trip
for the isolation cases. The two new UAT users stay restricted to TAJ.
Without this optional setup, record company-isolation cases as not yet covered.

## After testing

Keep the manifest and failed-case evidence. Finish/cancel only the remaining
active UAT trips with a UAT cleanup reason; a Manager cancels In Progress trips.
Archive the two UAT users and disposable masters and keep history as evidence.
Disable UAT report subscriptions and remove any test-delivery recipients. This
pack does not prevent double booking; do not leave duplicate auxiliary trips
running on the same vehicle.
