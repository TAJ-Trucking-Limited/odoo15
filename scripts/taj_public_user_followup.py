"""Focused read-only follow-up for the Public-user batch found on 18 September."""
import ast
import contextlib
import json
from pathlib import Path
import re
import sys
import tempfile

FOCUS_MOVES = [58363, 58366, 58369]
ORIGINAL_MOVES = [58296]


def source_snippets(source):
    """Select callback and bank-line creation methods, including decorators."""
    lines = source.splitlines()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        end = node.end_lineno
        body = '\n'.join(lines[start - 1:end])
        relevant = re.search(r'webhook|callback|auth\s*=\s*[\x27\x22]public', body, re.I)
        relevant = relevant or ('account.bank.statement.line' in body and '.create(' in body)
        relevant = relevant or (re.search(r'extract|ocr|digitiz', node.name, re.I) and
                                re.search(r'\.create\(|line_ids|_process|_fill_document', body))
        if relevant:
            yield start, end, lines[start - 1:min(end, start + 99)]


def inspect_sources():
    from odoo.modules.module import get_module_path
    remaining = 600
    for addon in ('account_bank_statement_extract', 'account_extract', 'iap_extract'):
        root = get_module_path(addon, display_warning=False)
        print('SOURCE_MODULE', json.dumps({'module': addon, 'path': root}), flush=True)
        if not root:
            continue
        for path in sorted(Path(root).rglob('*.py')):
            if any(p in ('tests', 'static', 'migrations', 'upgrades') for p in path.relative_to(root).parts):
                continue
            try:
                snippets = list(source_snippets(path.read_text(encoding='utf-8')))
            except (OSError, SyntaxError, UnicodeError) as exc:
                print('SOURCE_ERROR', type(exc).__name__, str(path), flush=True)
                continue
            for start, end, lines in snippets:
                if remaining <= 0:
                    print('SOURCE_TRUNCATED total line budget reached', flush=True)
                    return
                shown = min(remaining, len(lines))
                print('SOURCE_METHOD', json.dumps({'file': str(path), 'from': start, 'to': end,
                                                  'shown': shown}), flush=True)
                for offset, line in enumerate(lines[:shown]):
                    print('%s:%s: %s' % (path.name, start + offset, line), flush=True)
                remaining -= shown
                if shown < end - start + 1:
                    print('SOURCE_TRUNCATED method excerpt', flush=True)


def investigate(odoo_env):
    from psycopg2 import sql
    failures = []
    truncated = []

    def emit(label, value):
        print(label + ' ' + json.dumps(value, ensure_ascii=False, default=str), flush=True)

    with odoo_env.registry.cursor() as cr:
        cr.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cr.execute("SET LOCAL statement_timeout = '15s'")
        cr.execute("SET LOCAL lock_timeout = '2s'")
        cr.execute("SET LOCAL TimeZone = 'UTC'")

        def query(label, statement, params=()):
            cr.execute('SAVEPOINT followup_read')
            try:
                cr.execute(statement, params)
                return cr.dictfetchall()
            except Exception as exc:
                cr.execute('ROLLBACK TO SAVEPOINT followup_read')
                failures.append(label)
                emit('QUERY_ERROR', {'check': label, 'type': type(exc).__name__,
                                     'pgcode': getattr(exc, 'pgcode', None)})
                return []
            finally:
                cr.execute('RELEASE SAVEPOINT followup_read')

        schema = {}
        for row in query('schema', 'SELECT table_name,column_name FROM information_schema.columns '
                         'WHERE table_schema=current_schema()'):
            schema.setdefault(row['table_name'], set()).add(row['column_name'])

        def rows(label, table, fields, where, params=(), limit=200):
            if table not in schema:
                emit('SKIP', {'check': label, 'table': table})
                return []
            chosen = [f for f in fields.split() if f in schema[table]]
            result = query(label, sql.SQL('SELECT {} FROM {} WHERE {} ORDER BY id LIMIT %s').format(
                sql.SQL(',').join(map(sql.Identifier, chosen)), sql.Identifier(table), sql.SQL(where)),
                tuple(params) + (limit + 1,))
            if len(result) > limit:
                truncated.append(label)
                emit('TRUNCATED', {'check': label, 'limit': limit})
            result = result[:limit]
            emit(label, result)
            return result

        emit('START', {'focus_moves': FOCUS_MOVES, 'original_moves': ORIGINAL_MOVES})
        emit('DATABASE', query('database', "SELECT current_database() AS database, "
                               "current_setting('transaction_read_only') AS read_only"))
        audit = 'create_uid create_date write_uid write_date'
        focus = rows('PUBLIC_MOVES', 'account_move',
                     'id name ref date state journal_id company_id statement_line_id ' + audit,
                     'id=ANY(%s)', (FOCUS_MOVES,))
        moves = sorted({r['id'] for r in focus} | set(ORIGINAL_MOVES))
        bank = rows('PUBLIC_BANK_TRANSACTIONS', 'account_bank_statement_line',
                    'id move_id statement_id journal_id payment_ref amount amount_currency '
                    'currency_id foreign_currency_id is_reconciled unique_import_id ' + audit,
                    'move_id=ANY(%s)', (moves,))
        lines = rows('ALL_JOURNAL_ITEMS', 'account_move_line',
                     'id move_id account_id name balance debit credit amount_currency reconciled '
                     'matching_number full_reconcile_id reconcile_model_id ' + audit,
                     'move_id=ANY(%s)', (moves,))
        statements = sorted({r['statement_id'] for r in bank if r.get('statement_id')})
        rows('STATEMENTS', 'account_bank_statement',
             'id name date journal_id balance_start balance_end balance_end_real '
             'extract_state extract_status extract_attachment_id ' + audit,
             'id=ANY(%s)', (statements,))
        messages = rows('CHATTER', 'mail_message',
                        'id model res_id date author_id body ' + audit,
                        "(model='account.move' AND res_id=ANY(%s)) OR "
                        "(model='account.bank.statement.line' AND res_id=ANY(%s)) OR "
                        "(model='account.bank.statement' AND res_id=ANY(%s))",
                        (moves, [r['id'] for r in bank], statements), limit=300)
        rows('TRACKED_CHANGES', 'mail_tracking_value',
             'id mail_message_id field_id field_info old_value_integer new_value_integer '
             'old_value_float new_value_float old_value_char new_value_char ' + audit,
             'mail_message_id=ANY(%s)', ([r['id'] for r in messages],), limit=300)
        rows('ATTACHMENTS', 'ir_attachment',
             'id name mimetype file_size res_model res_id ' + audit,
             "(res_model='account.bank.statement' AND res_id=ANY(%s)) OR "
             "(res_model='account.move' AND res_id=ANY(%s))",
             (statements, moves))
        rows('RECONCILIATIONS', 'account_partial_reconcile',
             'id debit_move_id credit_move_id amount full_reconcile_id ' + audit,
             'debit_move_id=ANY(%s) OR credit_move_id=ANY(%s)',
             ([r['id'] for r in lines], [r['id'] for r in lines]))

        # Compare all same-label transactions in journal 8, including opposite signs.
        labels = sorted({r['payment_ref'] for r in bank if r.get('payment_ref')})
        rows('SAME_LABEL_TRANSACTIONS', 'account_bank_statement_line',
             'id move_id statement_id journal_id payment_ref amount is_reconciled ' + audit,
             'journal_id=8 AND payment_ref=ANY(%s)', (labels,))
        rows('PUBLIC_BATCH_SURVIVORS', 'account_bank_statement_line',
             'id move_id statement_id journal_id payment_ref amount is_reconciled ' + audit,
             "create_date >= '2026-09-18 14:31:00' AND create_date < '2026-09-18 14:34:00'")
        rows('VAT_RULE', 'account_reconcile_model',
             'id name active trigger match_label match_label_param company_id ' + audit,
             'id=30')
        rows('EXTRACTION_FIELD_DEFINITIONS', 'ir_model_fields',
             'id model name ttype relation',
             "(model ~ '^account[.]bank[.]statement' OR model='account.journal' OR model='ir.attachment') "
             "AND name ~* 'extract|digitiz|ocr'", limit=150)
        # Only status values are read; tokens, OCR payloads and binaries are omitted.
        for table, condition, params in [
            ('account_bank_statement', 'id=ANY(%s)', (statements,)),
            ('account_bank_statement_line', 'move_id=ANY(%s)', (moves,)),
            ('account_journal', 'id=8', ()),
        ]:
            status_fields = set(('extract_state', 'extract_status', 'extract_status_code',
                                 'extract_attachment_id', 'extract_error_code')) & schema.get(table, set())
            if status_fields:
                rows('EXTRACTION_STATUS_' + table.upper(), table,
                     'id ' + ' '.join(sorted(status_fields)), condition, params)
        accounts = sorted({r['account_id'] for r in lines if r.get('account_id')})
        rows('ACCOUNT_DIRECTORY', 'account_account', 'id name code code_store account_type reconcile',
             'id=ANY(%s)', (accounts,))
        users = {v for row in focus + bank + lines + messages for k, v in row.items()
                 if k in ('create_uid', 'write_uid') and v}
        authors = sorted({r['author_id'] for r in messages if r.get('author_id')})
        directory = rows('USER_DIRECTORY', 'res_users', 'id login partner_id active',
                         'id=ANY(%s) OR partner_id=ANY(%s)', (sorted(users), authors))
        partners = sorted(set(authors) | {r['partner_id'] for r in directory if r.get('partner_id')})
        rows('AUTHOR_DIRECTORY', 'res_partner', 'id name tz', 'id=ANY(%s)', (partners,))
        emit('END', {'query_errors': failures, 'truncated_checks': truncated,
                     'note': 'Database report only; source excerpts follow. No requests or callbacks are executed.'})
        cr.execute('ROLLBACK')


if 'env' in globals():
    with tempfile.NamedTemporaryFile(mode='w', prefix='taj_public_followup_', suffix='.txt',
                                     dir='/tmp', encoding='utf-8', delete=False) as report:
        console = sys.stdout

        class Output:
            def write(self, value):
                console.write(value)
                return report.write(value)

            def flush(self):
                console.flush()
                report.flush()

        print('REPORT_FILE ' + report.name, flush=True)
        try:
            with contextlib.redirect_stdout(Output()):
                investigate(env)
                inspect_sources()
        finally:
            print('REPORT_FILE ' + report.name, flush=True)
