from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('cfb', '0028_team_ats_records'),
    ]

    operations = [
        migrations.AddField(
            model_name='leaguerules',
            name='season_end_week',
            field=models.ForeignKey(
                blank=True,
                help_text="When this week's slate is final (or its end date has passed), the league season is final and prize money can show on standings",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='cfb.week',
            ),
        ),
    ]
