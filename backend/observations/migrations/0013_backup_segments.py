import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("observations", "0012_retention_boundary"),
        ("sessions", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="BackupSegment",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("scope", models.JSONField()),
                ("fields", models.JSONField()),
                ("snapshot_at", models.DateTimeField()),
                ("snapshot_version", models.PositiveBigIntegerField()),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("volume_device", models.PositiveBigIntegerField()),
                ("volume_inode", models.PositiveBigIntegerField()),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("preparing", "Preparing"),
                            ("available", "Available"),
                            ("blocked", "Blocked"),
                            ("removed", "Removed"),
                        ],
                        default="preparing",
                        max_length=12,
                    ),
                ),
                ("manifest", models.JSONField(null=True)),
                (
                    "actor",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="observations.product"
                    ),
                ),
            ],
            options={
                "default_permissions": (),
                "indexes": [models.Index(fields=["product", "state"], name="backup_product_state")],
            },
        ),
        migrations.CreateModel(
            name="RestoreIntent",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("target", models.UUIDField()),
                (
                    "session",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="sessions.session",
                    ),
                ),
                ("expires_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(null=True)),
                (
                    "actor",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "segment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="observations.backupsegment"
                    ),
                ),
            ],
            options={"default_permissions": ()},
        ),
        migrations.CreateModel(
            name="BackupAuthorityEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("version", models.PositiveBigIntegerField()),
                ("previous", models.CharField(max_length=64)),
                ("digest", models.CharField(max_length=64)),
                ("kind", models.CharField(max_length=32)),
                ("effective_at", models.DateTimeField()),
                ("rights", models.JSONField()),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="observations.product"
                    ),
                ),
            ],
            options={
                "default_permissions": (),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("product", "version"), name="backup_rights_sequence"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="BackupSubject",
            fields=[
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        serialize=False,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("identity", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
            ],
            options={"default_permissions": ()},
        ),
    ]
