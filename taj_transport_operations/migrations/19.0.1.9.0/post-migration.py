"""Retire the waiting-for-confirmation status without rewriting history."""


def migrate(cr, version):
    # Completed confirmations keep their historical outcome. Waiting rows
    # follow the movement already recorded; no departure time is invented.
    cr.execute("""
        UPDATE taj_transport_milestone
           SET state = CASE
               WHEN confirmed_at IS NOT NULL THEN 'completed'
               WHEN actual_departure_at IS NOT NULL THEN 'departed'
               WHEN actual_arrival_at IS NOT NULL THEN 'arrived'
               ELSE 'pending'
           END
         WHERE state = 'awaiting_confirmation'
    """)
