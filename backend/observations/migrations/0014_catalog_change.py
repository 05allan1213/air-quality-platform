import django.contrib.postgres.fields.ranges
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("observations", "0013_backup_segments"),
        ("accounts", "0004_governance"),
    ]

    operations = [
        migrations.CreateModel(
            name="QualityDecision",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("qualification_before", models.PositiveBigIntegerField()),
                ("qualification_after", models.PositiveBigIntegerField()),
                (
                    "action",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE, to="accounts.governanceaction"
                    ),
                ),
                (
                    "city_before",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="decisions_before",
                        to="observations.cityrevision",
                    ),
                ),
                (
                    "city_after",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="decisions_after",
                        to="observations.cityrevision",
                    ),
                ),
                (
                    "station_before",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="decisions_before",
                        to="observations.stationrevision",
                    ),
                ),
                (
                    "station_after",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="decisions_after",
                        to="observations.stationrevision",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=(
                            models.Q(
                                ("city_after__isnull", False),
                                ("city_before__isnull", False),
                                ("station_after__isnull", True),
                                ("station_before__isnull", True),
                            )
                            | models.Q(
                                ("city_after__isnull", True),
                                ("city_before__isnull", True),
                                ("station_after__isnull", False),
                                ("station_before__isnull", False),
                            )
                        ),
                        name="quality_decision_one_level",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="CatalogChange",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("active_period", django.contrib.postgres.fields.ranges.DateTimeRangeField()),
                (
                    "action",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="accounts.governanceaction"
                    ),
                ),
                (
                    "change",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE, to="observations.change"
                    ),
                ),
                (
                    "epoch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="observations.siteepoch"
                    ),
                ),
                (
                    "identifier",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="observations.stationidentifier",
                    ),
                ),
                (
                    "label",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="label_changes",
                        to="observations.stationidentifier",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("active_period__isempty", False),
                            ("active_period__lower_inc", True),
                            ("active_period__lower_inf", False),
                            ("active_period__upper_inc", False),
                        ),
                        name="catalogchange_half_open",
                    )
                ]
            },
        ),
    ]
