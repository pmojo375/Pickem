# Generated manually for team ATS record fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cfb', '0027_userprofile'),
    ]

    operations = [
        migrations.AddField(
            model_name='team',
            name='ats_wins',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='team',
            name='ats_losses',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='team',
            name='ats_pushes',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
