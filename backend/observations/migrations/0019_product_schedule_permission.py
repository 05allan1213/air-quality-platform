from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("collection", "0010_schedule_plans"),
        ("observations", "0018_citysearchalias"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="product",
            options={
                "permissions": [
                    ("manage_collection", "Manage collection jobs and pauses"),
                    ("run_collection", "Execute authorized collection jobs"),
                    ("configure_collection_schedule", "Configure collection schedules"),
                    ("import_history", "Import a controlled historical batch"),
                    ("review_observation", "Review authorized observation evidence"),
                    ("manage_grants", "Register and withdraw source uses"),
                ],
            },
        ),
    ]
