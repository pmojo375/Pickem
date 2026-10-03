from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cfb', '0032_alter_leaguerules_season_payout_last_percent'),
    ]

    operations = [
        migrations.AddField(
            model_name='game',
            name='down_distance_text',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='game',
            name='ball_on',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
    ]
