from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("observations", "0015_grant_registration")]
    operations = [
        # Existing rows have no verified generation. Keep their obligations in
        # PG and fail closed on read/cleanup rather than bind them to a new mount.
        migrations.AddField(
            "stagedinput", "volume_generation", models.UUIDField(null=True, editable=False)
        ),
        migrations.AddField(
            "backupsegment", "volume_generation", models.UUIDField(null=True, editable=False)
        ),
        migrations.RemoveField("backupsegment", "volume_device"),
        migrations.RemoveField("backupsegment", "volume_inode"),
    ]
