from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("observations", "0016_controlled_storage")]

    operations = [
        migrations.AddField(
            model_name="backupsegment",
            name="catalog_dependencies",
            field=models.JSONField(default=list),
        ),
    ]
