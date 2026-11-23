from django.db import migrations, models

# Keep S03's validation budget immutable. Permit only the one-time PG clock
# witness, including for grants that already had a validation allocation in S05.
WITNESS = """
  IF NEW.registered_at IS DISTINCT FROM OLD.registered_at THEN
    IF OLD.registered_at IS NULL
       AND NEW.registered_at BETWEEN statement_timestamp() AND clock_timestamp()
       AND (to_jsonb(NEW) - 'registered_at') = (to_jsonb(OLD) - 'registered_at') THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'immutable grant registration' USING ERRCODE = '23514';
  END IF;
"""


def freeze_validation(witness):
    return f"""
CREATE OR REPLACE FUNCTION cnemc_freeze_validation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  {witness}
  IF OLD.validation_disabled AND NOT NEW.validation_disabled OR NEW.validation_disabled AND NEW.max_total_requests IS NULL THEN
    RAISE EXCEPTION 'validation allocation closed' USING ERRCODE = '23514';
  END IF;
  IF (OLD.max_total_requests IS NOT NULL OR NEW.max_total_requests IS NOT NULL) AND
     (to_jsonb(NEW) - ARRAY['revoked_at','revoked_by_id','revoke_reason','validation_disabled']) IS DISTINCT FROM
     (to_jsonb(OLD) - ARRAY['revoked_at','revoked_by_id','revoke_reason','validation_disabled']) THEN
    RAISE EXCEPTION 'register a new validation grant' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("observations", "0014_catalog_change")]
    operations = [
        migrations.AddField(
            "usegrant", "registered_at", models.DateTimeField(null=True, editable=False)
        ),
        migrations.RunSQL(freeze_validation(WITNESS), freeze_validation("")),
        # These rows existed in S05 before the upgrade. Unprovable older boundaries
        # stay in legacy_boundary_review. New rows receive a post-commit witness.
        migrations.RunSQL(
            "UPDATE observations_usegrant SET registered_at = statement_timestamp()",
            migrations.RunSQL.noop,
        ),
    ]
