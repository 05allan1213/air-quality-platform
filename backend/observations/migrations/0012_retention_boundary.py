from django.db import migrations, models
from django.db.models import Min, Q


def identify_boundaries(apps, schema_editor):
    """The S05 scheduler recorded no grant FK. Link only unambiguous historical facts."""
    Job = apps.get_model("observations", "DispositionJob")
    Grant = apps.get_model("observations", "UseGrant")
    Product = apps.get_model("observations", "Product")
    for job in Job.objects.filter(
        completed_at=None, reason__in=["revocation", "rights_boundary"]
    ).iterator():
        grants = Grant.objects.filter(product_id=job.product_id)
        if job.reason == "revocation":
            matches = grants.filter(revoked_at=job.due_at)
        else:
            matches = grants.filter(
                Q(valid_until=job.due_at) | Q(retain_until=job.due_at) | Q(valid_from=job.due_at)
            )
        entries = list(matches.order_by("pk"))
        if entries and not any(
            g.operation in {"retain_raw", "retain_normalized", "backup", "restore"} for g in entries
        ):
            continue
        # A prior re-grant cannot be dated reliably from valid_from. Preserve old
        # evidence and fail closed pending an operator's independent rights reconciliation.
        job.reason = "legacy_boundary_review"
        job.save(update_fields=["reason"])
    for row in (
        Job.objects.filter(reason="legacy_boundary_review", completed_at=None)
        .values("product_id")
        .annotate(at=Min("due_at"))
    ):
        Product.objects.filter(pk=row["product_id"]).update(next_loss_at=row["at"])


class Migration(migrations.Migration):
    dependencies = [("observations", "0011_quality_governance")]
    operations = [
        migrations.AddField("product", "next_loss_at", models.DateTimeField(null=True)),
        migrations.AddField("product", "last_disposed_at", models.DateTimeField(null=True)),
        migrations.AddField(
            "dispositionjob",
            "boundary_grant",
            models.ForeignKey(null=True, on_delete=models.PROTECT, to="observations.usegrant"),
        ),
        migrations.AddField(
            "dispositionjob", "boundary_version", models.PositiveBigIntegerField(null=True)
        ),
        migrations.AddField("dispositionjob", "boundary_basis", models.JSONField(null=True)),
        migrations.RunPython(identify_boundaries, migrations.RunPython.noop),
    ]
