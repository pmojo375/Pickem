from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("cfb", "0034_multi_sport_foundation")]
    operations = [
        migrations.RenameField("leaguerules", "weekly_payout_percent", "period_payout_percent"),
        migrations.RenameField("leaguerules", "weekly_payout_structure", "period_payout_structure"),
        migrations.RemoveIndex(
            model_name='game',
            name='cfb_game_season__6dd8b3_idx',
        ),
        migrations.RemoveIndex(
            model_name='memberweek',
            name='cfb_memberw_league__e3fde4_idx',
        ),
        migrations.RemoveIndex(
            model_name='ranking',
            name='cfb_ranking_season__75741c_idx',
        ),

        migrations.RenameField("game", "kickoff", "start_time"),
        migrations.RenameModel("Week", "SchedulePeriod"),
        migrations.RenameModel("MemberWeek", "MemberPeriod"),
        migrations.RenameField("leaguerules", "drop_weeks", "drop_periods"),
        migrations.RenameField("leaguerules", "pickable_games_per_week", "pickable_games_per_period"),
        migrations.RenameField("leaguerules", "picks_per_week", "picks_per_period"),
        migrations.RenameField("leaguerules", "season_end_week", "season_end_period"),
        migrations.RenameField("game", "week", "schedule_period"),
        migrations.RenameField("game", "quarter", "period"),
        migrations.RenameField("gamespread", "week", "schedule_period"),
        migrations.RenameField("ranking", "week", "schedule_period"),
        migrations.RenameField("memberperiod", "week", "schedule_period"),
        migrations.RenameField("memberseason", "through_week", "through_period"),
    ]
