#!/bin/bash
# Phase 3 verification: fresh install, the full Phase 2 + Phase 3 suite, both
# static gates, byte-compilation, and a real Phase 2 -> Phase 3 upgrade drill.
#
# Everything the script needs is in this folder, in the worktree next to it, or
# in the Odoo environment named by ODOO_ENV. It is safe to re-run:
#
#   * the Phase 2 side of the drill is exported from the committed release
#     commit (a425a8c) with `git archive`, never from the working tree and
#     never from the git index;
#   * it only ever creates or drops the two databases named below, by exact
#     name, and refuses to touch an existing one unless RECREATE=1 is set;
#   * it refuses any database host that is not a local socket, so it cannot
#     reach a staging or production server.
#
# Usage:
#   bash phase3_verification/run_verification.sh
#
# Environment overrides:
#   ODOO_ENV    Odoo 19 environment (venv + Odoo source + local_addons)
#   DB_USER     PostgreSQL role                 (default: current user)
#   DB_HOST     local socket directory          (default: /tmp)
#   RECREATE=1  drop the two verification databases before starting
#   KEEP_DBS=1  keep them after the run (for inspection)
#   KEEP_TMP=1  keep the exported Phase 2 tree instead of deleting it
#
# Artifacts written next to this script:
#   01_fresh_install_tests.log          fresh install + full test suite
#   02_upgrade_19.0.1.6.4_to_19.0.1.7.0.log
#                                       the upgrade drill, stage by stage
#   02a_p2_install.log                  raw log, Phase 2 install
#   02b_p2_seed.log                     raw log, seeding the Phase 2 records
#   02c_upgrade.log                     raw log, -u to 19.0.1.7.0
#   02d_p3_check.log                    raw log, post-upgrade verification
#   02_p2_snapshot.txt / 02_p3_snapshot.txt
#                                       the snapshot before and after
#   03_static_gates.log                 04_compileall.log
#   05_verification_summary.log         environment, exit codes, PASS/FAIL
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
WORKTREE="$(cd "$HERE/.." && pwd)"
ODOO_ENV="${ODOO_ENV:-/private/var/folders/tr/w1_tv_4d3nd17pnhvw6l_fj40000gn/T/opencode/odoo19env}"
ODOO_SRC="$ODOO_ENV/odoo-19.0.post20261016"
LOCAL_ADDONS="$ODOO_ENV/local_addons"
PYTHON="$ODOO_ENV/venv/bin/python"
DB_USER="${DB_USER:-$(whoami)}"
DB_HOST="${DB_HOST:-/tmp}"
DB_PREFIX="taj_phase3_verify_"
FRESH_DB="${DB_PREFIX}fresh"
UPGRADE_DB="${DB_PREFIX}upgrade"
P2_COMMIT="a425a8c"                 # committed Phase 2 release
P2_EXPECTED_VERSION="19.0.1.6.4"
P3_EXPECTED_VERSION="19.0.1.7.0"
RECREATE="${RECREATE:-0}"
KEEP_DBS="${KEEP_DBS:-0}"
SUMMARY="$HERE/05_verification_summary.log"
DRILL_LOG="$HERE/02_upgrade_${P2_EXPECTED_VERSION}_to_${P3_EXPECTED_VERSION}.log"
P2_LOG="$HERE/02a_p2_install.log"
SEED_LOG="$HERE/02b_p2_seed.log"
UPGRADE_LOG="$HERE/02c_upgrade.log"
CHECK_LOG="$HERE/02d_p3_check.log"
P2_SNAPSHOT="$HERE/02_p2_snapshot.txt"
P3_SNAPSHOT="$HERE/02_p3_snapshot.txt"
GATE_LOG="$HERE/03_static_gates.log"
COMPILE_LOG="$HERE/04_compileall.log"
P2_DIR=""

cleanup() {
    if [ -n "$P2_DIR" ] && [ -d "$P2_DIR" ] && [ "${KEEP_TMP:-0}" != "1" ]; then
        rm -rf "$P2_DIR"
    fi
    for filtered in "${PRE_FILTERED:-}" "${POST_FILTERED:-}"; do
        if [ -n "$filtered" ] && [ -f "$filtered" ]; then
            rm -f "$filtered"
        fi
    done
}
trap cleanup EXIT INT TERM

die() {
    echo "run_verification: $*" >&2
    exit 2
}

FAILED=0
note() { printf '%s\n' "$*" | tee -a "$SUMMARY"; }

step() {  # step <label> <exit-code>
    if [ "$2" -eq 0 ]; then
        note "  OK    $1 (exit 0)"
    else
        note "  FAIL  $1 (exit $2)"
        FAILED=1
    fi
}

require_token() {  # require_token <file> <literal> <label>
    if grep -qF -- "$2" "$1"; then
        step "$3" 0
    else
        step "$3 (missing: $2)" 1
    fi
}

db_exists() {
    psql -h "$DB_HOST" -U "$DB_USER" -Atc \
        "SELECT 1 FROM pg_database WHERE datname = '$1'" 2>/dev/null | grep -qx 1
}

odoo_server() {  # odoo_server <db> <addons-path> <extra args...>
    db="$1"; addons="$2"; shift 2
    ( cd "$ODOO_SRC" && exec "$PYTHON" -m odoo server -d "$db" \
        --db_user="$DB_USER" --db_host="$DB_HOST" \
        --addons-path="odoo/addons,$LOCAL_ADDONS,$addons" \
        --no-http --max-cron-threads=0 --stop-after-init "$@" )
}

odoo_shell() {  # odoo_shell <db> <addons-path> <script> <mode>
    ( cd "$ODOO_SRC" && P2_DRILL_MODE="$4" exec "$PYTHON" -m odoo shell -d "$1" \
        --db_user="$DB_USER" --db_host="$DB_HOST" \
        --addons-path="odoo/addons,$LOCAL_ADDONS,$2" ) < "$3"
}

# ---------------------------------------------------------------- guards ---
[ -d "$ODOO_SRC" ] || die "Odoo source not found: $ODOO_SRC (set ODOO_ENV)"
[ -x "$PYTHON" ] || die "python not found: $PYTHON (set ODOO_ENV)"
[ -d "$LOCAL_ADDONS" ] || die "addons path not found: $LOCAL_ADDONS"
[ -f "$HERE/seed_p2_upgrade.py" ] || die "missing seed_p2_upgrade.py next to this script"
case "$DB_HOST" in
    /*) ;;
    *) die "refusing host '$DB_HOST': only a local socket may be used" ;;
esac
for db in "$FRESH_DB" "$UPGRADE_DB"; do
    case "$db" in
        "$DB_PREFIX"*) ;;
        *) die "refusing unsafe database name: $db" ;;
    esac
done

# --------------------------------------------------------------- summary ---
: > "$SUMMARY"
note "Phase 3 verification run"
note "  date            : $(date '+%Y-%m-%d %H:%M:%S %Z')"
note "  worktree        : $WORKTREE"
note "  odoo source     : $ODOO_SRC"
note "  odoo version    : $(cd "$ODOO_SRC" && "$PYTHON" -m odoo --version 2>&1 | head -1 || echo unknown)"
note "  python          : $("$PYTHON" -V 2>&1)"
note "  postgres client : $(psql --version 2>&1)"
note "  databases       : $FRESH_DB, $UPGRADE_DB on socket $DB_HOST as $DB_USER"
note "  phase 2 source  : commit $P2_COMMIT ($P2_EXPECTED_VERSION), exported with git archive"
note ""

for db in "$FRESH_DB" "$UPGRADE_DB"; do
    if db_exists "$db"; then
        if [ "$RECREATE" = "1" ]; then
            note "  dropping leftover verification database $db (RECREATE=1)"
            dropdb -h "$DB_HOST" -U "$DB_USER" "$db"
        else
            die "database $db already exists: remove it or re-run with RECREATE=1"
        fi
    fi
    createdb -h "$DB_HOST" -U "$DB_USER" "$db"
done

# ------------------------------------------- 1. fresh install + suite ------
note "1. fresh install + full Phase 2 + Phase 3 suite ($FRESH_DB)"
note "   $PYTHON -m odoo server -d $FRESH_DB ... -i taj_transport_operations"
note "   --test-enable --test-tags /taj_transport_operations"
if odoo_server "$FRESH_DB" "$WORKTREE" \
    -i taj_transport_operations --test-enable \
    --test-tags /taj_transport_operations \
    > "$HERE/01_fresh_install_tests.log" 2>&1; then
    rc=0
else
    rc=$?
fi
step "fresh install + test suite" "$rc"
RESULT_LINE="$(grep -E 'odoo\.tests\.result:' "$HERE/01_fresh_install_tests.log" | tail -1 || true)"
note "   $RESULT_LINE"
case "$RESULT_LINE" in
    *"0 failed, 0 error(s)"*) ;;
    *) step "test suite reported failures" 1 ;;
esac
RUN_TESTS="$(printf '%s' "$RESULT_LINE" | sed -n 's/.*of \([0-9][0-9]*\) tests.*/\1/p')"
note "   tests executed: ${RUN_TESTS:-unknown}"
note ""

# ------------------------------------------------- 2. upgrade drill --------
note "2. upgrade drill $P2_EXPECTED_VERSION -> $P3_EXPECTED_VERSION ($UPGRADE_DB)"
P2_DIR="$(mktemp -d "${TMPDIR:-/tmp}/taj_phase3_p2.XXXXXX")"
PRE_FILTERED="$(mktemp)"
POST_FILTERED="$(mktemp)"

: > "$DRILL_LOG"
{
    echo "=== environment ==="
    echo "worktree     : $WORKTREE"
    echo "odoo source  : $ODOO_SRC"
    echo "database     : $UPGRADE_DB (socket $DB_HOST, role $DB_USER)"
    echo "phase 2 tree : commit $P2_COMMIT exported with git archive to $P2_DIR"
    echo
    echo "=== stage 1: export and install $P2_EXPECTED_VERSION ==="
} >> "$DRILL_LOG"
# The whole committed tree is exported, not just the module: the Phase 2 tree
# also carries the sibling addons the module depends on (move_invoice_line,
# fleet_navirec, ...), exactly as the frozen release shipped them.
git -C "$WORKTREE" archive "$P2_COMMIT" | tar -x -C "$P2_DIR" \
    || die "could not export $P2_COMMIT"
P2_MANIFEST_VERSION="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' \
    "$P2_DIR/taj_transport_operations/__manifest__.py" | head -1)"
[ "$P2_MANIFEST_VERSION" = "$P2_EXPECTED_VERSION" ] \
    || die "commit $P2_COMMIT declares $P2_MANIFEST_VERSION, expected $P2_EXPECTED_VERSION"
echo "exported manifest version: $P2_MANIFEST_VERSION" >> "$DRILL_LOG"
if odoo_server "$UPGRADE_DB" "$P2_DIR" -i taj_transport_operations > "$P2_LOG" 2>&1; then
    rc=0
else
    rc=$?
fi
tail -3 "$P2_LOG" >> "$DRILL_LOG"
echo "EXIT_P2_INSTALL=$rc" >> "$DRILL_LOG"
step "stage 1: install $P2_EXPECTED_VERSION" "$rc"

if grep -Eq 'CRITICAL|Traceback' "$P2_LOG"; then
    step "stage 1 log contains errors" 1
else
    step "stage 1 log is clean" 0
fi

{
    echo
    echo "=== stage 2: seed Phase 2 records (Sale Order, 2 trips, assignment) ==="
} >> "$DRILL_LOG"
if odoo_shell "$UPGRADE_DB" "$P2_DIR" "$HERE/seed_p2_upgrade.py" seed \
    > "$SEED_LOG" 2>&1; then
    rc=0
else
    rc=$?
fi
grep '^SNAP ' "$SEED_LOG" > "$P2_SNAPSHOT" || true
cat "$P2_SNAPSHOT" >> "$DRILL_LOG"
echo "EXIT_SEED=$rc" >> "$DRILL_LOG"
step "stage 2: seed Phase 2 records" "$rc"
if grep -Eq 'Traceback' "$SEED_LOG"; then
    step "seed log contains a traceback" 1
else
    step "seed log is clean" 0
fi
require_token "$P2_SNAPSHOT" "SNAP version $P2_EXPECTED_VERSION" \
    "stage 2: seeded database runs $P2_EXPECTED_VERSION"
require_token "$P2_SNAPSHOT" "SNAP order " "stage 2: sale order snapshot"
require_token "$P2_SNAPSHOT" "2100.00 sale" "stage 2: sale order is 2100.00 / confirmed"

{
    echo
    echo "=== stage 3: upgrade to $P3_EXPECTED_VERSION from the worktree ==="
} >> "$DRILL_LOG"
if odoo_server "$UPGRADE_DB" "$WORKTREE" -u taj_transport_operations \
    > "$UPGRADE_LOG" 2>&1; then
    rc=0
else
    rc=$?
fi
grep -E 'odoo\.modules\.loading: module taj_transport_operations|Modules loaded|modules loaded' \
    "$UPGRADE_LOG" | tail -3 >> "$DRILL_LOG"
echo "EXIT_UPGRADE=$rc" >> "$DRILL_LOG"
step "stage 3: upgrade to $P3_EXPECTED_VERSION" "$rc"
if grep -Eq 'CRITICAL|Traceback' "$UPGRADE_LOG"; then
    step "upgrade log contains errors" 1
else
    step "upgrade log is clean" 0
fi

{
    echo
    echo "=== stage 4: post-upgrade verification ($P3_EXPECTED_VERSION) ==="
} >> "$DRILL_LOG"
if odoo_shell "$UPGRADE_DB" "$WORKTREE" "$HERE/seed_p2_upgrade.py" check \
    > "$CHECK_LOG" 2>&1; then
    rc=0
else
    rc=$?
fi
grep '^SNAP ' "$CHECK_LOG" > "$P3_SNAPSHOT" || true
cat "$P3_SNAPSHOT" >> "$DRILL_LOG"
echo "EXIT_CHECK=$rc" >> "$DRILL_LOG"
step "stage 4: post-upgrade verification" "$rc"
require_token "$P3_SNAPSHOT" "SNAP mode check" \
    "stage 4: the post-upgrade pass ran in verification mode"
require_token "$CHECK_LOG" "SNAPCHECK OK" \
    "stage 4: every in-database assertion held"
require_token "$P3_SNAPSHOT" "SNAP version $P3_EXPECTED_VERSION" \
    "stage 4: database runs $P3_EXPECTED_VERSION"
require_token "$P3_SNAPSHOT" "SNAP milestones 0 0" \
    "stage 4: no checkpoint was backfilled on Phase 2 trips"
require_token "$P3_SNAPSHOT" "SNAP routes 0" \
    "stage 4: no route template was created"
require_token "$P3_SNAPSHOT" "SNAP events 0" \
    "stage 4: no milestone event was created"
require_token "$P3_SNAPSHOT" "SNAP route_free False False" \
    "stage 4: Phase 2 trips are still valid without a route"
require_token "$P3_SNAPSHOT" "SNAP progress 0.0 0.0" \
    "stage 4: progress of Phase 2 trips stays 0.0"

{
    echo
    echo "=== stage 5: snapshot diff (sale, trips, ids, assignment) ==="
    echo "compared labels: order|lines|trips|ids|assignment"
} >> "$DRILL_LOG"
grep -E '^SNAP (order|lines|trips|ids|assignment) ' "$P2_SNAPSHOT" > "$PRE_FILTERED" || true
grep -E '^SNAP (order|lines|trips|ids|assignment) ' "$P3_SNAPSHOT" > "$POST_FILTERED" || true
if diff -u "$PRE_FILTERED" "$POST_FILTERED" >> "$DRILL_LOG" 2>&1; then
    rc=0
else
    rc=$?
fi
step "stage 5: Phase 2 records unchanged by the upgrade" "$rc"
{
    echo
    echo "=== result ==="
} >> "$DRILL_LOG"
{
    echo "PRE-UPGRADE  ($P2_EXPECTED_VERSION)"
    sed 's/^/  /' "$P2_SNAPSHOT"
    echo "POST-UPGRADE ($P3_EXPECTED_VERSION)"
    sed 's/^/  /' "$P3_SNAPSHOT"
} >> "$DRILL_LOG"
note ""

# ------------------------------------------- 3. static gates ---------------
note "3. static gates"
cd "$WORKTREE"
{
    echo "=== Phase 2 static gate ==="
    if "$PYTHON" scripts/taj_transport_phase2_static_gate.py; then rc_p2=0; else rc_p2=$?; fi
    echo "EXIT_P2_GATE=$rc_p2"
    echo
    echo "=== Phase 3 static gate ==="
    if "$PYTHON" scripts/taj_transport_phase3_static_gate.py; then rc_p3=0; else rc_p3=$?; fi
    echo "EXIT_P3_GATE=$rc_p3"
} > "$GATE_LOG" 2>&1 || true
GATE_P2_RC="$(sed -n 's/^EXIT_P2_GATE=//p' "$GATE_LOG" | tail -1)"
GATE_P3_RC="$(sed -n 's/^EXIT_P3_GATE=//p' "$GATE_LOG" | tail -1)"
step "Phase 2 static gate" "${GATE_P2_RC:-1}"
step "Phase 3 static gate" "${GATE_P3_RC:-1}"
GATE_CASES="$(sed -n 's/^Defined Odoo test cases: \([0-9][0-9]*\).*/\1/p' "$GATE_LOG" | tail -1)"
note "   test cases defined in the module: ${GATE_CASES:-unknown}"
note "   tests executed by the suite    : ${RUN_TESTS:-unknown}"
if [ -n "$RUN_TESTS" ] && [ "$RUN_TESTS" = "$GATE_CASES" ]; then
    step "every defined test case ran" 0
else
    step "test count mismatch (gate $GATE_CASES vs run $RUN_TESTS)" 1
fi
note ""

# ------------------------------------------- 4. byte-compilation ----------
note "4. byte-compilation"
{
    echo "=== python -m compileall -q taj_transport_operations scripts ==="
    if "$PYTHON" -m compileall -q taj_transport_operations scripts; then rc=0; else rc=$?; fi
    echo "EXIT_COMPILEALL=$rc"
    echo
    echo "=== python ==="
    "$PYTHON" -V
} > "$COMPILE_LOG" 2>&1 || true
COMPILE_RC="$(sed -n 's/^EXIT_COMPILEALL=//p' "$COMPILE_LOG" | tail -1)"
step "compileall" "${COMPILE_RC:-1}"
note ""

# ------------------------------------------- cleanup and verdict ----------
if [ "$KEEP_DBS" = "1" ]; then
    note "keeping $FRESH_DB and $UPGRADE_DB (KEEP_DBS=1)"
else
    dropdb -h "$DB_HOST" -U "$DB_USER" --if-exists "$FRESH_DB"
    dropdb -h "$DB_HOST" -U "$DB_USER" --if-exists "$UPGRADE_DB"
    note "dropped $FRESH_DB and $UPGRADE_DB"
fi
note ""
note "logs: 01_fresh_install_tests.log, $(basename "$DRILL_LOG"), 02a-02d raw stages,"
note "      02_p2_snapshot.txt, 02_p3_snapshot.txt, 03_static_gates.log, 04_compileall.log"
if [ "$FAILED" -eq 0 ]; then
    note "VERIFICATION RESULT: PASS"
else
    note "VERIFICATION RESULT: FAIL"
fi
exit "$FAILED"
