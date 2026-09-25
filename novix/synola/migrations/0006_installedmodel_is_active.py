from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("synola", "0005_installedmodel_metadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="installedmodel",
            name="is_active",
            field=models.BooleanField(default=False),
        ),
    ]