from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("collection", "0005_frozen_job")]
    operations = [
        migrations.RunSQL(
            # Old Workers counted each known-unsent quota denial as an attempt.
            # Discount those ancestors along retry edges only. Keep UUIDs, lineage,
            # reservations, results and source evidence. Beat rechecks admission.
            """
            WITH RECURSIVE legacy_denials AS (
                SELECT id, attempt
                FROM collection_run
                WHERE status = 'failed' AND error_code = 'budget_exhausted'
                  AND request_state = 'unsent' AND reserved_at IS NULL
            ), affected(id, attempt, discount) AS (
                SELECT id, attempt, 0 FROM legacy_denials
                UNION ALL
                SELECT child.id, child.attempt,
                       parent.discount + (denial.id IS NOT NULL)::integer
                FROM affected parent
                JOIN collection_run child ON child.predecessor_id = parent.id
                LEFT JOIN legacy_denials denial ON denial.id = parent.id
                WHERE child.reason = 'retry' AND child.attempt = parent.attempt + 1
            ), corrections AS (
                SELECT id, max(discount) AS discount FROM affected GROUP BY id
            )
            UPDATE collection_run r
            SET attempt = r.attempt - c.discount,
                next_retry_at = CASE
                    WHEN r.next_retry_at IS NULL AND NOT w.disposed
                         AND NOT EXISTS (SELECT 1 FROM collection_run child
                                         WHERE child.predecessor_id = r.id AND child.reason = 'retry')
                         AND (EXISTS (SELECT 1 FROM legacy_denials denial WHERE denial.id = r.id)
                              OR (c.discount > 0 AND r.attempt = 3
                                  AND ((r.status = 'failed' AND r.error_code IN
                                        ('upstream_transient', 'timeout', 'database_error'))
                                       OR (r.status = 'expired' AND r.error_code = 'lease_expired'))))
                    THEN statement_timestamp()
                    ELSE r.next_retry_at
                END
            FROM corrections c, collection_workwindow w
            WHERE r.id = c.id AND r.window_id = w.id
            """,
            reverse_sql=migrations.RunSQL.noop,
        )
    ]
