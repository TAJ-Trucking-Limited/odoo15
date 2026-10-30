"""Reuse milestone audit history without inventing operational activity."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    cr.execute("""
        INSERT INTO taj_transport_trip_activity (
            trip_id, company_id, milestone_id, source_event_id, activity_type,
            description, user_id, recorded_at, occurred_at,
            create_uid, write_uid, create_date, write_date
        )
        SELECT milestone.trip_id, milestone.company_id, milestone.id, event.id,
               event.event_type,
               CASE event.event_type WHEN 'arrival' THEN 'Arrival: '
                    ELSE 'Departure: ' END || milestone.name ||
               CASE WHEN COALESCE(event.note, '') != '' THEN E'\n' || event.note ELSE '' END,
               event.user_id, COALESCE(event.recorded_at, event.create_date), event.occurred_at,
               event.user_id, event.user_id,
               COALESCE(event.recorded_at, event.create_date),
               COALESCE(event.recorded_at, event.create_date)
          FROM taj_transport_milestone_event AS event
          JOIN taj_transport_milestone AS milestone ON milestone.id = event.milestone_id
         WHERE event.event_type IN ('arrival', 'departure')
        ON CONFLICT (source_event_id) DO NOTHING
    """)
    cr.execute("""
        UPDATE taj_transport_trip AS trip
           SET last_activity_at = activity.recorded_at
          FROM (
              SELECT trip_id, MAX(recorded_at) AS recorded_at
                FROM taj_transport_trip_activity GROUP BY trip_id
          ) AS activity
         WHERE trip.id = activity.trip_id
           AND (trip.last_activity_at IS NULL OR trip.last_activity_at < activity.recorded_at)
        RETURNING trip.id
    """)
    trips = api.Environment(cr, SUPERUSER_ID, {})["taj.transport.trip"].browse([
        row[0] for row in cr.fetchall()
    ])
    trips.invalidate_recordset(["last_activity_at"])
    trips.modified(["last_activity_at"])
