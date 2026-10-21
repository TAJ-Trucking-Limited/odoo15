"""Discard obsolete transient dialogs before removing their action choices."""


def migrate(cr, version):
    cr.execute("SELECT to_regclass('taj_transport_milestone_action_wizard')")
    if not cr.fetchone()[0]:
        return
    # These are unsaved operational dialogs, not checkpoints or audit events.
    cr.execute("""
        DELETE FROM taj_transport_milestone_action_wizard
         WHERE action_type IN ('confirmation', 'cancel_confirmation')
    """)
