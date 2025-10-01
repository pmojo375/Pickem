# Generated manually to add league rules
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cfb', '0009_rename_cfb_leagueg_league__idx_cfb_leagueg_league__495c15_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='league',
            name='key_pick_extra_points',
            field=models.IntegerField(default=1, help_text='Extra points for correct key picks'),
        ),
        migrations.AddField(
            model_name='league',
            name='key_picks_enabled',
            field=models.BooleanField(default=True, help_text='Allow users to designate key picks for bonus points'),
        ),
        migrations.AddField(
            model_name='league',
            name='number_of_key_picks',
            field=models.IntegerField(default=1, help_text='Number of key picks allowed per week'),
        ),
        migrations.AddField(
            model_name='league',
            name='pickable_games_per_week',
            field=models.IntegerField(default=10, help_text='Maximum number of games available for picking each week'),
        ),
        migrations.AddField(
            model_name='league',
            name='picks_per_week',
            field=models.IntegerField(default=0, help_text='Number of picks required per week (0 = must pick all available games)'),
        ),
        migrations.AddField(
            model_name='league',
            name='points_per_correct_pick',
            field=models.IntegerField(default=1, help_text='Points awarded for each correct pick'),
        ),
        migrations.AddField(
            model_name='league',
            name='spread_lock_weekday',
            field=models.IntegerField(choices=[(0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'), (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday')], default=2, help_text='Day of the week when spreads lock in place'),
        ),
    ]

