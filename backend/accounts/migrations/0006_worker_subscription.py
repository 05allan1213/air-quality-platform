from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_service_pulse")]

    operations = [
        migrations.AddField(
            model_name="servicepulse",
            name="subscribed_queues",
            field=models.JSONField(null=True),
        ),
    ]
