from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("collection", "0004_controlevent")]
    operations = [
        migrations.RunSQL(
            """
        CREATE FUNCTION s03_freeze_job() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF ROW(NEW.product_id,NEW.city_id,NEW.profile,NEW.executor_id,NEW.expected_codes,NEW.roster_basis,NEW.cadence_seconds)
             IS DISTINCT FROM ROW(OLD.product_id,OLD.city_id,OLD.profile,OLD.executor_id,OLD.expected_codes,OLD.roster_basis,OLD.cadence_seconds)
             AND EXISTS (SELECT 1 FROM collection_run WHERE job_id=OLD.id)
          THEN RAISE EXCEPTION 'create a new job configuration' USING ERRCODE = '23514'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER s03_freeze_job BEFORE UPDATE ON collection_job
          FOR EACH ROW EXECUTE FUNCTION s03_freeze_job();
    """,
            """
        DROP TRIGGER s03_freeze_job ON collection_job;
        DROP FUNCTION s03_freeze_job();
    """,
        )
    ]
