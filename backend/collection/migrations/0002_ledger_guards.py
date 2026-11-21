from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("collection", "0001_initial"),
        ("observations", "0008_product_collection_pause"),
    ]
    operations = [
        migrations.RunSQL(
            """
        ALTER TABLE collection_runitem ADD CONSTRAINT s03_item_same_window
            FOREIGN KEY (run_id, window_id) REFERENCES collection_run(id, window_id);
        CREATE FUNCTION s03_preserve_acceptance() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.accepted AND OLD IS DISTINCT FROM NEW THEN
                RAISE EXCEPTION 'immutable collection acceptance' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER s03_preserve_acceptance BEFORE UPDATE ON collection_runitem
            FOR EACH ROW EXECUTE FUNCTION s03_preserve_acceptance();
        CREATE FUNCTION s03_receipt_lineage() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.collection_item_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM collection_runitem i JOIN collection_run r ON r.id=i.run_id
                WHERE i.id=NEW.collection_item_id AND r.batch_id=NEW.batch_id
            ) THEN RAISE EXCEPTION 'inconsistent collection receipt' USING ERRCODE = '23514'; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER s03_receipt_lineage BEFORE INSERT OR UPDATE OF batch_id,collection_item_id
            ON observations_receipt FOR EACH ROW EXECUTE FUNCTION s03_receipt_lineage();
    """,
            """
        DROP TRIGGER s03_receipt_lineage ON observations_receipt;
        DROP FUNCTION s03_receipt_lineage();
        DROP TRIGGER s03_preserve_acceptance ON collection_runitem;
        DROP FUNCTION s03_preserve_acceptance();
        ALTER TABLE collection_runitem DROP CONSTRAINT s03_item_same_window;
    """,
        )
    ]
