"""Run inside an Odoo.sh Python shell; no installation or module update required.

    exec(compile(open('/tmp/taj_public_user_investigation.py').read(),
                 'taj_public_user_investigation.py', 'exec'))

All database reads use a separate, PostgreSQL-enforced READ ONLY transaction.
No ORM record reads, business methods, external requests, or credential columns.
The report is printed and saved to a private file in /tmp on the Odoo.sh build.
"""
import contextlib
import html
import json
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone


# Screenshot evidence. Change these only if investigating a different example.
LINE_IDS = [182507, 182521]
MOVE_IDS = []                    # Optional parent account.move IDs from record URLs.
STATEMENT_LINE_IDS = []          # Optional account.bank.statement.line IDs.
LABEL = 'VAT On Fees On EFT Transaction August 2026 - Salaries'
EVENT_START_UTC = '2026-09-17 00:00:00'
EVENT_END_UTC = '2026-09-20 00:00:00'  # Exclusive; wide enough for screenshot timezone.
TRANSACTION_DATE = '2026-08-28'
AMOUNT = 8370
ROW_LIMIT = 150
TARGET_LIMIT = 40


def investigate(odoo_env):
    from psycopg2 import sql

    errors = []
    limits = []
    people = set()
    partner_ids = set()
    rule_ids = set()
    account_ids = set()
    company_ids = set()
    journal_ids = set()
    move_ids = set(MOVE_IDS)
    bank_ids = set(STATEMENT_LINE_IDS)
    message_ids = set()

    def emit(label, value):
        print(label + ' ' + json.dumps(value, ensure_ascii=False, default=str), flush=True)

    with odoo_env.registry.cursor() as cr:
        # This is deliberately the first statement on a separate connection.
        cr.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cr.execute("SET LOCAL statement_timeout = '15s'")
        cr.execute("SET LOCAL lock_timeout = '2s'")
        cr.execute("SET LOCAL TimeZone = 'UTC'")

        def query(label, statement, params=()):
            # SQL savepoints avoid ORM flushes and let independent checks continue.
            cr.execute('SAVEPOINT public_user_diagnostic')
            try:
                cr.execute(statement, params)
                rows = cr.dictfetchall()
            except Exception as exc:
                cr.execute('ROLLBACK TO SAVEPOINT public_user_diagnostic')
                detail = {'check': label, 'error': type(exc).__name__,
                          'pgcode': getattr(exc, 'pgcode', None)}
                errors.append(detail)
                emit('QUERY_ERROR', detail)
                rows = []
            finally:
                cr.execute('RELEASE SAVEPOINT public_user_diagnostic')
            return rows

        schema = {}
        for row in query('schema', '''
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = current_schema()
        '''):
            schema.setdefault(row['table_name'], set()).add(row['column_name'])

        def has(table, *fields):
            return table in schema and set(fields).issubset(schema[table])

        def collect(rows):
            for row in rows:
                for key in ('create_uid', 'write_uid', 'user_id'):
                    if row.get(key):
                        people.add(row[key])
                if row.get('author_id'):
                    partner_ids.add(row['author_id'])
                for key, target in [('reconcile_model_id', rule_ids),
                                    ('account_id', account_ids), ('company_id', company_ids),
                                    ('journal_id', journal_ids)]:
                    if row.get(key):
                        target.add(row[key])

        def capped(label, statement, params=(), limit=ROW_LIMIT):
            statement = sql.SQL(statement) if isinstance(statement, str) else statement
            rows = query(label, sql.SQL('{} LIMIT %s').format(statement),
                         tuple(params) + (limit + 1,))
            if len(rows) > limit:
                limits.append(label)
                emit('TRUNCATED', {'check': label, 'limit': limit})
            rows = rows[:limit]
            collect(rows)
            return rows

        def sample(label, table, fields, where='TRUE', params=(), limit=ROW_LIMIT,
                   order='id'):
            if not has(table, 'id'):
                emit('SKIP', {'check': label, 'reason': 'table absent', 'table': table})
                return []
            selected = [field for field in fields.split() if field in schema[table]]
            if 'id' not in selected:
                selected.insert(0, 'id')
            rows = query(label, sql.SQL('SELECT {} FROM {} WHERE {} ORDER BY {} LIMIT %s').format(
                sql.SQL(', ').join(map(sql.Identifier, selected)), sql.Identifier(table),
                sql.SQL(where), sql.SQL(order)), tuple(params) + (limit + 1,))
            if len(rows) > limit:
                limits.append(label)
                emit('TRUNCATED', {'check': label, 'limit': limit})
            rows = rows[:limit]
            collect(rows)
            emit(label, {'rows': rows, 'returned': len(rows)})
            return rows

        audit = 'create_uid create_date write_uid write_date'
        bank_fields = ('id move_id statement_id journal_id company_id payment_ref date '
                       'amount amount_currency currency_id foreign_currency_id is_reconciled '
                       'unique_import_id transaction_type ' + audit)
        line_fields = ('id move_id name date account_id debit credit balance amount_currency '
                       'currency_id partner_id reconciled matching_number full_reconcile_id '
                       'reconcile_model_id statement_line_id payment_id tax_line_id '
                       'tax_repartition_line_id ' + audit)
        msg_fields = 'id model res_id date message_type subtype_id author_id subject body ' + audit

        emit('START', {'utc': datetime.now(timezone.utc), 'known_line_ids': LINE_IDS,
                       'label': LABEL, 'event_utc_range': [EVENT_START_UTC, EVENT_END_UTC],
                       'scope': 'all companies visible in this database; targeted, capped SQL reads'})
        emit('DATABASE', query('database', '''
            SELECT current_database() AS database, current_schema() AS schema,
                   current_setting('transaction_read_only') AS read_only,
                   current_setting('transaction_isolation') AS isolation,
                   current_setting('TimeZone') AS database_timezone,
                   current_setting('statement_timeout') AS statement_timeout
        ''')[0])
        emit('INTERPRETATION', [
            'Database timestamps below are UTC. Accounting dates are calendar dates.',
            'Chatter author_id is a res.partner ID; create_uid/write_uid are res.users IDs.',
            'Creation identity is the database execution identity, not proof of a human or HTTP origin.',
            'OdooBot attribution alone does not distinguish manual actions from automation.',
            'Latest write metadata is not a complete audit history.',
            'Duplicate candidates are leads, not confirmed duplicates.',
            'Current rule/access/job configuration does not prove its state on the event date.',
        ])

        identities = sample('BUILTIN_IDENTITIES', 'ir_model_data',
                            'id module name model res_id',
                            "module = 'base' AND name IN ('public_user','user_root','user_admin')")
        public_ids = {r['res_id'] for r in identities
                      if r['name'] == 'public_user' and r['model'] == 'res.users'}
        people.update(r['res_id'] for r in identities if r['model'] == 'res.users')
        public_users = sample('PUBLIC_USER', 'res_users', 'id login partner_id active share ' + audit,
                              'id = ANY(%s)', (sorted(public_ids),))
        public_partners = {r['partner_id'] for r in public_users if r.get('partner_id')}
        partner_ids.update(public_partners)

        original_lines = sample('KNOWN_LINES', 'account_move_line', line_fields,
                                'id = ANY(%s)', (LINE_IDS,))
        move_ids.update(r['move_id'] for r in original_lines if r.get('move_id'))
        known_bank = sample('KNOWN_BANK_TRANSACTIONS', 'account_bank_statement_line', bank_fields,
                            'id = ANY(%s)', (sorted(bank_ids),))
        move_ids.update(r['move_id'] for r in known_bank if r.get('move_id'))

        # Chatter can survive deletion of a journal item, so recover the parent there.
        if has('mail_message', 'model', 'res_id', 'body'):
            body_parts = ['body ILIKE %s'] + ['body ~ %s' for _ in LINE_IDS]
            seeds = sample('RECOVERY_MESSAGES', 'mail_message', msg_fields,
                           "model IN ('account.move','account.bank.statement.line') AND (" +
                           ' OR '.join(body_parts) + ')',
                           tuple(['%' + LABEL + '%'] +
                                 [r'(^|[^0-9])' + str(i) + r'([^0-9]|$)' for i in LINE_IDS]))
            for row in seeds:
                message_ids.add(row['id'])
                if row['model'] == 'account.move':
                    move_ids.add(row['res_id'])
                else:
                    bank_ids.add(row['res_id'])
        if has('account_bank_statement_line', 'payment_ref'):
            candidates = sample('LABEL_BANK_TRANSACTIONS', 'account_bank_statement_line',
                                bank_fields, 'payment_ref ILIKE %s', ('%' + LABEL + '%',))
            move_ids.update(r['move_id'] for r in candidates if r.get('move_id'))
            bank_ids.update(r['id'] for r in candidates)
        if has('account_move', 'ref'):
            candidates = sample('LABEL_JOURNAL_ENTRIES', 'account_move', 'id name ref ' + audit,
                                'ref ILIKE %s', ('%' + LABEL + '%',))
            move_ids.update(r['id'] for r in candidates)
        if bank_ids:
            recovered_bank = sample('RECOVERED_BANK_TRANSACTIONS', 'account_bank_statement_line',
                                    bank_fields, 'id = ANY(%s)', (sorted(bank_ids),))
            move_ids.update(r['move_id'] for r in recovered_bank if r.get('move_id'))
        if len(move_ids) > TARGET_LIMIT:
            limits.append('target_moves')
            emit('TRUNCATED', {'check': 'target_moves', 'limit': TARGET_LIMIT,
                               'found': len(move_ids)})
            move_ids = set(sorted(move_ids)[:TARGET_LIMIT])
        emit('TARGETS', {'move_ids': sorted(move_ids),
                         'missing_line_ids': sorted(set(LINE_IDS) - {r['id'] for r in original_lines})})
        if not move_ids:
            emit('FINDING', 'No parent entries recovered. Check configured IDs/label and pre-deletion backups; '
                 'this does not establish that no accounting issue occurred.')

        moves = sample('JOURNAL_ENTRIES', 'account_move',
                       'id name ref date state move_type journal_id company_id currency_id '
                       'statement_line_id payment_id reversed_entry_id posted_before ' + audit,
                       'id = ANY(%s)', (sorted(move_ids),))
        lines = sample('JOURNAL_ITEMS', 'account_move_line', line_fields,
                       'move_id = ANY(%s)', (sorted(move_ids),), limit=500)
        bank = sample('BANK_TRANSACTIONS', 'account_bank_statement_line', bank_fields,
                      'move_id = ANY(%s)', (sorted(move_ids),))
        bank_ids.update(r['id'] for r in bank)
        statement_ids = sorted({r['statement_id'] for r in bank if r.get('statement_id')})
        sample('BANK_STATEMENTS', 'account_bank_statement',
               'id name date journal_id company_id balance_start balance_end '
               'balance_end_real state ' + audit, 'id = ANY(%s)', (statement_ids,))
        messages = sample('CHATTER', 'mail_message', msg_fields,
                          "(model = 'account.move' AND res_id = ANY(%s)) OR "
                          "(model = 'account.bank.statement.line' AND res_id = ANY(%s))",
                          (sorted(move_ids), sorted(bank_ids)), limit=400, order='id')
        message_ids.update(r['id'] for r in messages)
        sample('PLANNED_ACTIVITIES', 'mail_activity',
               'id res_model res_model_id res_id activity_type_id user_id summary date_deadline ' + audit,
               'res_id = ANY(%s)' +
               (" AND res_model = 'account.move'" if has('mail_activity', 'res_model') else
                " AND res_model_id IN (SELECT id FROM ir_model WHERE model = 'account.move')"),
               (sorted(move_ids),))
        if has('ir_attachment', 'res_model', 'res_id'):
            sample('ATTACHMENT_METADATA', 'ir_attachment',
                   'id name mimetype file_size res_model res_id ' + audit,
                   "(res_model = 'account.move' AND res_id = ANY(%s)) OR "
                   "(res_model = 'account.bank.statement.line' AND res_id = ANY(%s)) OR "
                   "(res_model = 'account.bank.statement' AND res_id = ANY(%s))",
                   (sorted(move_ids), sorted(bank_ids), statement_ids))
        for row in messages:
            emit('CHATTER_TEXT', {'message_id': row['id'], 'model': row.get('model'),
                                  'res_id': row.get('res_id'),
                                  'text': html.unescape(re.sub('<[^>]+>', ' ', row.get('body') or '')).strip()})
        sample('TRACKED_CHANGES', 'mail_tracking_value',
               'id mail_message_id field_id field field_desc field_type field_info '
               'old_value_integer new_value_integer old_value_float new_value_float '
               'old_value_char new_value_char old_value_text new_value_text '
               'old_value_datetime new_value_datetime ' + audit,
               'mail_message_id = ANY(%s)', (sorted(message_ids),), limit=500)

        if has('account_move_line', 'move_id', 'balance'):
            emit('ENTRY_BALANCES', query('entry_balances', '''
                SELECT move_id, count(*) AS line_count, sum(balance) AS balance_sum
                FROM account_move_line WHERE move_id = ANY(%s) GROUP BY move_id ORDER BY move_id
            ''', (sorted(move_ids),)))
        focus_line_ids = {r['id'] for r in lines} | set(LINE_IDS)
        partials = []
        if has('account_partial_reconcile', 'debit_move_id', 'credit_move_id'):
            partials = sample('PARTIAL_RECONCILIATIONS', 'account_partial_reconcile',
                              'id debit_move_id credit_move_id amount debit_amount_currency '
                              'credit_amount_currency full_reconcile_id max_date exchange_move_id ' + audit,
                              'debit_move_id = ANY(%s) OR credit_move_id = ANY(%s)',
                              (sorted(focus_line_ids), sorted(focus_line_ids)), limit=400)
        counterpart_ids = {r[k] for r in partials for k in ('debit_move_id', 'credit_move_id') if r.get(k)}
        counterpart = sample('MATCHED_COUNTERPART_ITEMS', 'account_move_line', line_fields,
                             'id = ANY(%s)', (sorted(counterpart_ids - focus_line_ids),), limit=400)
        counterpart_moves = {r['move_id'] for r in counterpart if r.get('move_id')}
        sample('MATCHED_COUNTERPART_ENTRIES', 'account_move',
               'id name ref date state move_type journal_id company_id ' + audit,
               'id = ANY(%s)', (sorted(counterpart_moves),))
        full_ids = {r['full_reconcile_id'] for r in partials + lines if r.get('full_reconcile_id')}
        fulls = sample('FULL_RECONCILIATIONS', 'account_full_reconcile',
                       'id name exchange_move_id ' + audit, 'id = ANY(%s)', (sorted(full_ids),))
        exchange_ids = {r['exchange_move_id'] for r in partials + fulls if r.get('exchange_move_id')}
        sample('EXCHANGE_ENTRIES', 'account_move',
               'id name ref date state journal_id company_id ' + audit,
               'id = ANY(%s)', (sorted(exchange_ids),))
        if has('account_move', 'tax_cash_basis_rec_id'):
            sample('CASH_BASIS_ENTRIES', 'account_move',
                   'id name ref date state tax_cash_basis_rec_id ' + audit,
                   'tax_cash_basis_rec_id = ANY(%s)', ([r['id'] for r in partials],))

        # Scope of Public user activity, with full counts and capped examples.
        for table in ('account_move', 'account_move_line', 'account_bank_statement_line',
                      'account_partial_reconcile', 'account_full_reconcile'):
            if has(table, 'create_uid', 'create_date'):
                emit('PUBLIC_CREATION_TOTALS', {'table': table, 'rows': query('public_totals_' + table,
                    sql.SQL('SELECT create_uid, count(*) AS count, min(create_date) AS first_utc, '
                            'max(create_date) AS last_utc FROM {} WHERE create_uid = ANY(%s) '
                            'GROUP BY create_uid').format(sql.Identifier(table)), (sorted(public_ids),))})
                sample('PUBLIC_EVENT_' + table.upper(), table,
                       'id name move_id journal_id company_id account_id balance amount '
                       'debit_move_id credit_move_id full_reconcile_id ' + audit,
                       'create_uid = ANY(%s) AND create_date >= %s AND create_date < %s',
                       (sorted(public_ids), EVENT_START_UTC, EVENT_END_UTC))
            if has(table, 'write_uid', 'write_date'):
                sample('PUBLIC_LAST_EDIT_' + table.upper(), table,
                       'id name move_id journal_id company_id account_id balance amount ' + audit,
                       'write_uid = ANY(%s) AND write_date >= %s AND write_date < %s',
                       (sorted(public_ids), EVENT_START_UTC, EVENT_END_UTC))
        if has('mail_message', 'author_id', 'create_date'):
            emit('PUBLIC_MESSAGE_COUNTS', capped('public_messages', '''
                SELECT model, create_uid, count(*) AS count,
                       min(create_date) AS first_utc, max(create_date) AS last_utc
                FROM mail_message WHERE author_id = ANY(%s)
                  AND create_date >= %s AND create_date < %s
                GROUP BY model, create_uid ORDER BY count(*) DESC
            ''', (sorted(public_partners), EVENT_START_UTC, EVENT_END_UTC)))

        # Identify nearby batch writes using actual creation times, not the accounting date.
        event_times = {r.get('create_date') for r in moves + bank if r.get('create_date')}
        if len(event_times) > 8:
            limits.append('batch_windows')
            emit('TRUNCATED', {'check': 'batch_windows', 'limit': 8})
        for stamp in sorted(event_times)[:8]:
            emit('BATCH_WINDOW', {'from_utc': stamp - timedelta(minutes=2),
                                  'to_utc': stamp + timedelta(minutes=2)})
            if has('account_move', 'create_date', 'create_uid', 'journal_id'):
                batch_counts = capped('batch_counts', '''
                    SELECT create_uid, journal_id, count(*) AS count,
                           min(create_date) AS first_utc, max(create_date) AS last_utc
                    FROM account_move WHERE create_date BETWEEN %s AND %s
                    GROUP BY create_uid, journal_id ORDER BY count(*) DESC
                ''', (stamp - timedelta(minutes=2), stamp + timedelta(minutes=2)))
                collect(batch_counts)
                emit('BATCH_COUNTS', batch_counts)
            if has('account_move', 'create_date', 'journal_id'):
                sample('BATCH_JOURNAL_ENTRIES', 'account_move',
                       'id name ref date state journal_id company_id ' + audit,
                       'create_date BETWEEN %s AND %s AND journal_id = ANY(%s)',
                       (stamp - timedelta(minutes=2), stamp + timedelta(minutes=2), sorted(journal_ids)))

        # Same journal and day/amount; do not declare repeated amounts to be duplicates.
        if has('account_bank_statement_line', 'move_id', 'amount', 'payment_ref') and has('account_move', 'date', 'journal_id'):
            emit('DUPLICATE_CANDIDATES', capped('duplicate_candidates', '''
                SELECT b.id AS statement_line_id, b.move_id, m.name, m.date,
                       m.journal_id, b.payment_ref, b.amount, b.create_uid, b.create_date
                FROM account_bank_statement_line b JOIN account_move m ON m.id = b.move_id
                WHERE m.journal_id = ANY(%s) AND m.date = %s
                  AND abs(abs(b.amount) - %s) < 0.005
                ORDER BY b.id
            ''', (sorted(journal_ids), TRANSACTION_DATE, AMOUNT)))
        if has('account_bank_statement_line', 'unique_import_id'):
            emit('REPEATED_IMPORT_IDENTIFIERS', capped('repeated_import_ids', '''
                SELECT unique_import_id, count(*) AS count, array_agg(id ORDER BY id) AS ids
                FROM account_bank_statement_line
                WHERE unique_import_id IN (
                    SELECT unique_import_id FROM account_bank_statement_line WHERE id = ANY(%s)
                ) AND unique_import_id IS NOT NULL AND unique_import_id <> ''
                GROUP BY unique_import_id HAVING count(*) > 1
            ''', (sorted(bank_ids),)))
        if has('account_move_line', 'balance', 'account_id', 'move_id') and has('account_move', 'date', 'journal_id'):
            emit('SAME_DAY_ACCOUNT_ITEMS', capped('same_day_accounts', '''
                SELECT l.id, l.move_id, m.name, m.ref, m.date, m.journal_id,
                       l.account_id, l.balance, m.create_uid, m.create_date
                FROM account_move_line l JOIN account_move m ON m.id = l.move_id
                WHERE m.date = %s AND l.account_id = ANY(%s)
                  AND abs(abs(l.balance) - %s) < 0.005 ORDER BY l.id
            ''', (TRANSACTION_DATE, sorted(account_ids), AMOUNT)))

        # Rule configuration and linked rule messages, including archived VAT rules.
        if has('account_reconcile_model', 'name'):
            rules = sample('RECONCILIATION_MODELS', 'account_reconcile_model',
                           'id name active company_id sequence trigger auto_reconcile rule_type '
                           'match_label match_label_param match_note match_note_param '
                           'match_transaction_type match_transaction_type_param match_amount '
                           'match_amount_min match_amount_max match_partner match_same_currency '
                           'allow_payment_tolerance payment_tolerance_param ' + audit,
                           'id = ANY(%s) OR name::text ILIKE %s', (sorted(rule_ids), '%VAT%'))
            rule_ids.update(r['id'] for r in rules)
        sample('RECONCILIATION_MODEL_LINES', 'account_reconcile_model_line',
                            'id model_id account_id label amount_type amount force_tax_included '
                            'partner_id analytic_distribution ' + audit,
                            'model_id = ANY(%s)', (sorted(rule_ids),))
        sample('RECONCILIATION_MODEL_CHATTER', 'mail_message', msg_fields,
               "model = 'account.reconcile.model' AND res_id = ANY(%s)", (sorted(rule_ids),))
        for table in sorted(schema):
            if ('reconcile' in table and 'rel' in table and
                    any('journal' in c for c in schema[table])):
                model_col = next((c for c in ('account_reconcile_model_id', 'model_id')
                                  if c in schema[table]), None)
                journal_col = next((c for c in ('account_journal_id', 'journal_id')
                                    if c in schema[table]), None)
                if model_col and journal_col:
                    emit('RECONCILIATION_JOURNAL_SCOPE', {'table': table, 'rows': capped('rule_journals',
                        sql.SQL('SELECT {}, {} FROM {} WHERE {} = ANY(%s)').format(
                            sql.Identifier(model_col), sql.Identifier(journal_col),
                            sql.Identifier(table), sql.Identifier(model_col)), (sorted(rule_ids),))})

        # Current public permissions. Recursively include implied group membership.
        effective_groups = []
        if has('res_groups_users_rel', 'uid', 'gid'):
            if has('res_groups_implied_rel', 'gid', 'hid'):
                effective_groups = query('public_groups', '''
                    WITH RECURSIVE groups(gid) AS (
                        SELECT gid FROM res_groups_users_rel WHERE uid = ANY(%s)
                        UNION
                        SELECT r.hid FROM res_groups_implied_rel r JOIN groups g ON r.gid = g.gid
                    ) SELECT gid FROM groups ORDER BY gid
                ''', (sorted(public_ids),))
            else:
                effective_groups = query('public_groups',
                    'SELECT DISTINCT gid FROM res_groups_users_rel WHERE uid = ANY(%s)', (sorted(public_ids),))
        group_ids = [r['gid'] for r in effective_groups]
        sample('PUBLIC_EFFECTIVE_GROUPS', 'res_groups', 'id name category_id privilege_id',
               'id = ANY(%s)', (group_ids,))
        if has('ir_model', 'model'):
            models = sample('ACCOUNTING_MODELS', 'ir_model', 'id model name',
                            "model IN ('account.move','account.move.line','account.bank.statement',"
                            "'account.bank.statement.line','account.partial.reconcile',"
                            "'account.full.reconcile','account.reconcile.model')")
            model_ids = [r['id'] for r in models]
            access = sample('PUBLIC_ACCOUNTING_ACLS', 'ir_model_access',
                            'id name model_id group_id active perm_read perm_write perm_create perm_unlink ' + audit,
                            'model_id = ANY(%s) AND (group_id IS NULL OR group_id = ANY(%s))',
                            (model_ids, group_ids))
            if any(r.get('active', True) and (r.get('perm_write') or r.get('perm_create') or r.get('perm_unlink'))
                   for r in access):
                emit('FINDING', 'Public identity has a current accounting write/create/delete ACL grant. '
                     'Record rules may restrict scope; sudo can bypass both ACLs and record rules.')
            sample('ACCOUNTING_RECORD_RULES', 'ir_rule',
                   'id name model_id active global domain_force perm_read perm_write perm_create perm_unlink ' + audit,
                   'model_id = ANY(%s)', (model_ids,))
            if has('rule_group_rel', 'rule_group_id', 'group_id'):
                emit('RECORD_RULE_GROUPS', capped('record_rule_groups', '''
                    SELECT r.rule_group_id, r.group_id FROM rule_group_rel r
                    JOIN ir_rule i ON i.id = r.rule_group_id WHERE i.model_id = ANY(%s)
                ''', (model_ids,), limit=500))
        else:
            model_ids = []

        # Print action metadata and suspicious token names, never action source or webhook URLs.
        action_fields = 'id name state model_id binding_model_id base_automation_id usage ' + audit
        action_conditions, action_params = [], []
        if has('ir_act_server', 'model_id'):
            action_conditions.append('model_id = ANY(%s)')
            action_params.append(model_ids)
        if has('ir_act_server', 'code'):
            action_conditions.append("code ~* %s")
            action_params.append(r'account\.(move|bank|partial|full|reconcile)|public_user|with_user|sudo\(')
        actions = sample('RELEVANT_SERVER_ACTIONS', 'ir_act_server', action_fields,
                         ' OR '.join(action_conditions) or 'FALSE', tuple(action_params))
        action_ids = [r['id'] for r in actions]
        if action_ids and has('ir_act_server', 'code'):
            codes = query('action_markers', 'SELECT id, code FROM ir_act_server WHERE id = ANY(%s)', (action_ids,))
            for row in codes:
                emit('ACTION_CODE_MARKERS', {'id': row['id'], 'markers': [token for token in
                     ('public_user', 'with_user', 'sudo(', 'account.move', 'account.bank', 'reconcile', 'author_id')
                     if token in (row.get('code') or '')]})
        cron_conditions, cron_params = [], []
        if has('ir_cron', 'user_id'):
            cron_conditions.append('user_id = ANY(%s)')
            cron_params.append(sorted(public_ids))
        if has('ir_cron', 'ir_actions_server_id'):
            cron_conditions.append('ir_actions_server_id = ANY(%s)')
            cron_params.append(action_ids)
        sample('RELEVANT_SCHEDULED_ACTIONS', 'ir_cron',
               'id name cron_name ir_actions_server_id user_id active interval_number interval_type '
               'nextcall lastcall failure_count first_failure_date ' + audit,
               ' OR '.join(cron_conditions) or 'FALSE', tuple(cron_params))
        if has('base_automation', 'model_id'):
            sample('ACCOUNTING_AUTOMATIONS', 'base_automation',
                   'id name model_id active trigger action_server_id filter_pre_domain filter_domain ' + audit,
                   'model_id = ANY(%s)', (model_ids,))
        else:
            emit('SKIP', {'check': 'ACCOUNTING_AUTOMATIONS', 'reason': 'base_automation table absent'})
        if has('ir_logging', 'create_date'):
            log_conditions = ["create_date >= %s AND create_date < %s"]
            log_params = [EVENT_START_UTC, EVENT_END_UTC]
            if has('ir_logging', 'name'):
                log_conditions.append("name ~* %s")
                log_params.append('account|reconcil|import|cron|automat')
            sample('DATABASE_LOG_METADATA', 'ir_logging',
                   'id create_date level type name path func line',
                   ' AND '.join(log_conditions), tuple(log_params))
        sample('INSTALLED_ACCOUNTING_AND_CUSTOM_MODULES', 'ir_module_module',
               'id name state latest_version installed_version ' + audit,
               "state = 'installed' AND (name ~* 'account|bank|reconcil|audit|import|automat|taj|website')")

        # Decode identifiers after all sections have contributed IDs.
        sample('JOURNAL_DIRECTORY', 'account_journal',
               'id name code type company_id currency_id default_account_id suspense_account_id ' + audit,
               'id = ANY(%s)', (sorted(journal_ids),))
        sample('ACCOUNT_DIRECTORY', 'account_account',
               'id name code code_store account_type reconcile deprecated active',
               'id = ANY(%s)', (sorted(account_ids),))
        sample('COMPANY_DIRECTORY', 'res_company', 'id name currency_id partner_id',
               'id = ANY(%s)', (sorted(company_ids),))
        users = sample('USER_DIRECTORY', 'res_users', 'id login partner_id active share ' + audit,
                       'id = ANY(%s) OR partner_id = ANY(%s)',
                       (sorted(people), sorted(partner_ids)), limit=300)
        partner_ids.update(r['partner_id'] for r in users if r.get('partner_id'))
        partners = sample('AUTHOR_DIRECTORY', 'res_partner', 'id name active tz',
                          'id = ANY(%s)', (sorted(partner_ids),), limit=300)
        if has('res_partner', 'tz'):
            emit('TIMEZONE_NOTE', {'partner_timezones': sorted({r['tz'] for r in partners if r.get('tz')}),
                                   'screenshot_time': '2026-09-18 17:32 (viewer timezone unconfirmed)'})
        emit('NEXT_EVIDENCE', [
            'Compare JOURNAL_ENTRIES/BANK_TRANSACTIONS create_uid with CHATTER author_id via the directories.',
            'Check whether the VAT rule is linked on JOURNAL_ITEMS and inspect its conditions/accounts.',
            'Compare duplicate candidates with the actual bank statement; repeated amounts alone are insufficient.',
            'Use BATCH_WINDOW timestamps to inspect Odoo.sh odoo.log, retained HTTP logs, imports and deployments.',
            'Inspect custom public HTTP routes plus elevated writes if the underlying creator is Public user.',
            'Deleted rows/reconciliations may require a backup restored into an isolated staging database.',
        ])
        emit('LIMITATIONS', [
            'No HTTP request/session/IP, import-file provenance, or deleted-row history is inferred from user IDs.',
            'Server filesystem logs, backups and custom controller source are outside this database report.',
            'The reconciliation graph includes directly linked partials and full records; it is not recursively expanded.',
            'Missing version-specific fields are omitted. Missing optional tables produce SKIP lines.',
            'Samples are bounded. Inspect TRUNCATED/QUERY_ERROR and narrow configuration if needed.',
            'No automated conclusion that a transaction is fraudulent, duplicated, or incorrectly taxed.',
        ])
        emit('END', {'completed': True, 'query_errors': len(errors), 'truncated_checks': limits,
                     'checks_without_errors_or_truncation': not errors and not limits,
                     'note': 'completed means execution finished; findings still require review'})
        # Roll back even the read-only transaction explicitly before cursor exit.
        cr.execute('ROLLBACK')


if 'env' in globals():
    # Stream progress and save the same report, with owner-only file permissions.
    with tempfile.NamedTemporaryFile(mode='w', prefix='taj_public_user_investigation_',
                                     suffix='.txt', dir='/tmp', encoding='utf-8', delete=False) as handle:
        console = sys.stdout

        class ReportOutput:
            def write(self, text):
                console.write(text)
                return handle.write(text)

            def flush(self):
                console.flush()
                handle.flush()

        print('REPORT_FILE ' + handle.name, flush=True)
        try:
            with contextlib.redirect_stdout(ReportOutput()):
                investigate(env)
        finally:
            print('\nREPORT_FILE ' + handle.name, flush=True)
