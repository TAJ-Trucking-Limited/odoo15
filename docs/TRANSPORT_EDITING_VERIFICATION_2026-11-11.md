# TAJ transport editing verification — November 11, 2026

The implementation was delegated to `ui_edits` (GPT-6 Luna), `core_edits` (GPT-6.1 Terra), and `fleet_pairing` (GPT-6.1 Terra). The primary agent reviewed and verified their changes, and a separate Terra reviewer checked the combined patch. The reviewer found an audit gap when switching between analytical accounts with identical names. The implementer corrected it to compare account IDs, and the regression passed.

The reviewed patch is in the `19_upgrade` working directory. It upgrades `taj_transport_operations` to `19.0.1.44.0`. There was no commit, push, or deployment.

| Requested behavior | Implementation and evidence |
|---|---|
| Date-only ETA and ETOL | ETA is a Date on Sales, shipment file rows, and Trips; ETOL stays a Date. Upgrade retains old timestamps in `sale_order_line.eta_timestamp_backup` and converts them to Damascus calendar dates. A migration test covers midnight rollover, empty values, and rerunning the guarded conversion. |
| Analytical account after confirmation | Users with the existing Sales and analytical account access can change the account on a locked order. The actual Odoo Form save, direct write, restricted nested write, audit, company boundary, and rejection of mixed commercial writes were exercised. |
| Hide Size and clear Weight | Size is optional and hidden in the Sales line list. Both Clear modes clear Weight and Container Number on the copy. The source line stays unchanged. |
| Numeric Weight | Plain decimals, zero, and empty values work on Sales and file rows. Text and units are rejected on save. An unrelated edit preserves a preexisting text weight. |
| Exclusive Fleet pairing | Default Trailer on trucks and Assigned Truck on trailers use one stored relationship. Replacement, clearing, invalid resources, company/ACL checks, and concurrent uniqueness were exercised. Pairing leaves trip assignments and availability unchanged. |
| File numbers | New files use `FN/YYYY/MM/NNNNN` with the existing continuous counter. Month rollover and counter continuity are tested; existing references are kept. |
| Wider lists | The three dispatch/summary lists share scoped wrapping and column widths. Views parsed and compiled. Live browser sizing and screenshots remain to be checked. |
| Inline ETOL | A Transport user can save ETOL from the Without Allocation list through the audited shipment path, without allocating a truck or creating a trip. The view limits edits to ETOL; permission and allocation-state tests passed. |
| Draft checkpoint progress | Draft rows hide the checkpoint state badge in Trips and progress bar in Trips Summary. The Trip Status badge remains visible. View tests verify the per-row conditions. |

## Test results

- **Final focused transport run:** 111 tests, zero failures and zero errors. This includes the new features, form save and audit checks, and seven Sales/accounting integration tests covering invoice and VAT behavior.
- **Transport baseline:** 406 tests on the original code, with 13 failures and 4 errors. The broader implementation run reproduced those same failing transport test names and introduced none at that point. The final focused run covers the subsequent audit and fixture changes.
- **Separate accounting baseline:** The legacy `move_invoice_line` suite had 23 errors on the original code with Transport installed, including goods fixtures without a File Type and incompatible report test calls. These broader suite issues were outside this change. The targeted transport accounting integration tests passed.
- **Legacy static gates:** Both already failed on the original `19.0.1.43.0` manifest because they expect historical versions. They were left unchanged. Python compilation, XML parsing, and `git diff --check` passed on the integrated patch.
- **Disposable database upgrade:** Verified module version `19.0.1.44.0`, the ETA Date column, the timestamp backup column, and the new sequence prefix and padding. The clone had no nonempty historical ETA values; the migration test used a representative nonempty timestamp. Review real historical date samples and any unit-bearing legacy weights before a production upgrade.
- **Visual check:** The browser access host was unavailable. The wider columns, optional Size, inline date editor, and draft progress displays still need a live browser check for appearance and interaction.

The local test database was `codex_taj_entry_20261111`; the accounting baseline used `codex_taj_baseinvoice_20261111`. The implementation, review, and test artifacts are in `/private/tmp/taj-transport-edits-20261111`, including `final-focused.log`, `baseline.log`, `baseline-accounting.log`, and `final-review.md`. The production database was not used.
