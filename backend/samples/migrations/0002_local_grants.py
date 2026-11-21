from django.db import migrations
from django.utils import timezone

SCOPE = {
    "product": "F",
    "source": "local.fixture.v1",
    "geography": "synthetic",
    "audience": "local_authenticated",
}
OPERATIONS = ("process_local", "retain", "display")


def seed(apps, schema_editor):
    grants = apps.get_model("samples", "FixtureGrant").objects.using(schema_editor.connection.alias)
    for operation in OPERATIONS:
        grants.create(
            **SCOPE,
            operation=operation,
            enabled=True,
            valid_from=timezone.now(),
            basis="Self-authored co-hour-v1, local authenticated demonstration only",
        )


def unseed(apps, schema_editor):
    apps.get_model("samples", "FixtureGrant").objects.using(schema_editor.connection.alias).filter(
        **SCOPE, operation__in=OPERATIONS
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("samples", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
