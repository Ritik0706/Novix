from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("synola", "0004_installedmodel"),
    ]

    operations = [
        migrations.AddField(
            model_name="installedmodel",
            name="architecture",
            field=models.CharField(default="", blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="installedmodel",
            name="quantization",
            field=models.CharField(default="", blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="installedmodel",
            name="chat_template",
            field=models.CharField(default="", blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="installedmodel",
            name="capabilities",
            field=models.JSONField(default=dict),
        ),
    ]