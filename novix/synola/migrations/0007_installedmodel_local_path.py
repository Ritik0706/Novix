from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("synola", "0006_installedmodel_is_active"),
    ]

    operations = [
        migrations.AddField(
            model_name="installedmodel",
            name="local_path",
            field=models.CharField(default="", blank=True, max_length=500),
        ),
    ]