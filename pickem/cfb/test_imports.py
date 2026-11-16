"""All provider HTTP calls are mocked. These tests never consume a real API quota."""
from datetime import date, timedelta
from io import StringIO
from unittest.mock import Mock, patch

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from django.core.cache import cache

from cfb.models import Sport, Season, Team, Game, SchedulePeriod, League, Pick, Ranking, TeamStat, MemberPeriod, MemberSeason, MemberSeasonPayment, LeagueRules
from django.contrib.auth import get_user_model
from cfb.services.imports import sync_sport_data
from cfb.services.providers.basketball import BasketballProvider
from cfb.services.providers.base import ProviderError


@override_settings(CFBD_API_KEY="fixture-only-key")
class BasketballImportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.sport = Sport.objects.get(pk="mens-college-basketball")
        self.season = Season.objects.create(sport=self.sport, year=2027, is_active=True)
        self.team_rows = [
            {"id": 1, "sourceId": "127", "school": "Michigan State", "abbreviation": "MSU", "logos": ["https://example.org/msu.png"]},
            {"id": 2, "sourceId": "150", "school": "Duke"},
        ]
        self.game_rows = [{"id": 44, "sourceId": "espn-44", "season": 2027, "seasonType": "postseason", "startDate": "2027-03-20T19:00:00Z", "homeTeamId": 1, "homeTeam": "Michigan State", "awayTeamId": 2, "awayTeam": "Duke", "homeSeed": 7, "awaySeed": 2, "tournament": "NCAA", "neutralSite": True, "status": "scheduled"}]
        self.line_rows = [{"gameId": 44, "lines": [{"provider": "consensus", "spread": 0, "overUnder": 140, "spreadOpen": -1, "overUnderOpen": 141}]}]
        self.start, self.end = date(2027, 3, 1), date(2027, 4, 10)
        self.http = self.enterContext(patch("requests.Session.get"))
        self.http.side_effect = lambda url, **kwargs: self.response(self.team_rows if url.endswith("/teams") else self.line_rows if url.endswith("/lines") else self.game_rows)

    def response(self, data, status=200):
        response = Mock(status_code=status, headers={})
        response.json.return_value = data
        return response

    def sync(self, **kwargs):
        return sync_sport_data(self.season, start=self.start, end=self.end, **kwargs)

    def test_shared_key_and_bounded_basketball_parameters(self):
        client = BasketballProvider()
        self.assertEqual(client.session.headers["Authorization"], "Bearer fixture-only-key")
        self.sync(tournament="NCAA", season_type="postseason", include_lines=True)
        self.assertEqual(self.http.call_count, 3)
        params = self.http.call_args_list[1].kwargs["params"]
        self.assertEqual(params["season"], 2027)
        self.assertEqual(params["tournament"], "NCAA")
        self.assertIn("startDateRange", params)
        self.assertNotIn("year", params)
        game = Game.objects.get()
        self.assertEqual(game.tournament, "NCAA")
        self.assertEqual(game.home_seed, 7)
        self.assertEqual(game.current_home_spread, 0)
        self.assertEqual(game.opening_home_spread, -1)
        self.assertEqual(game.provider_ids, {"cbbd": "44", "espn": "espn-44"})
        self.assertEqual(game.schedule_period.kind, "day")
        self.assertEqual(game.sport_id, self.sport.pk)

    def test_repeated_sync_is_idempotent_and_cached(self):
        self.sync(include_lines=True)
        result = self.sync(include_lines=True)
        self.assertEqual(result["requests"], 0)
        self.assertEqual(self.http.call_count, 3)
        self.assertEqual(Game.objects.count(), 1)
        self.assertEqual(Team.objects.count(), 2)
        self.assertEqual(Game.objects.get().spreads.count(), 1)

    def test_empty_responses_are_cached(self):
        self.http.return_value = self.response([])
        self.http.side_effect = None
        client = BasketballProvider()
        self.assertEqual(client.teams(2027), [])
        self.assertEqual(client.teams(2027), [])
        self.assertEqual(self.http.call_count, 1)

    def test_rate_limit_stops_without_retry_and_is_shared_with_football(self):
        self.http.side_effect = None
        self.http.return_value = self.response([], 429)
        with self.assertRaises(ProviderError):
            self.sync()
        from cfb.services.providers.base import QuotaClient
        with self.assertRaises(ProviderError):
            QuotaClient("https://api.collegefootballdata.com", "cfbd").get("/teams", {})
        self.assertEqual(self.http.call_count, 1)
        self.assertEqual(Team.objects.count(), 0)

    def test_auth_failure_does_not_retry(self):
        self.http.side_effect = None
        self.http.return_value = self.response([], 401)
        with self.assertRaises(ProviderError):
            self.sync()
        self.assertEqual(self.http.call_count, 1)

    def test_budget_prevents_additional_calls_and_partial_writes(self):
        with self.assertRaises(ProviderError):
            self.sync(include_lines=True, max_requests=2)
        self.assertEqual(self.http.call_count, 2)
        self.assertEqual(Team.objects.count(), 0)

    def test_malformed_game_rolls_back_all_storage(self):
        self.game_rows[0]["startDate"] = "invalid"
        with self.assertRaises(ProviderError):
            self.sync()
        self.assertEqual(Team.objects.count(), 0)
        self.season.refresh_from_db()
        self.assertFalse(self.season.teams_pulled)

    def test_stable_id_allows_team_rename(self):
        self.sync()
        before = Team.objects.get(provider_ids__cbbd="1")
        self.team_rows[0]["school"] = "MSU Renamed"
        sync_sport_data(self.season, teams_only=True, force=True)
        self.assertEqual(Team.objects.get(provider_ids__cbbd="1").pk, before.pk)
        self.assertEqual(Team.objects.count(), 2)

    def test_different_sport_can_have_same_provider_ids(self):
        football = Season.objects.create(year=2027, is_active=True)
        Team.objects.create(season=football, name="Michigan State", provider_ids={"cbbd": "1"})
        self.sync()
        self.assertEqual(Team.objects.filter(name="Michigan State").count(), 2)

    def test_import_preserves_custom_round(self):
        round_ = SchedulePeriod.objects.create(season=self.season, number=1, kind="round", name="Round of 64", season_type="postseason", start_date=date(2027, 3, 18), end_date=date(2027, 3, 21))
        self.sync()
        self.assertEqual(Game.objects.get().schedule_period, round_)

    def test_scheduled_provider_snapshot_preserves_live_scores(self):
        self.sync()
        game = Game.objects.get()
        game.home_score, game.away_score, game.period = 20, 19, 1
        game.save()
        self.sync(force=True)
        game.refresh_from_db()
        self.assertEqual((game.home_score, game.away_score, game.period), (20, 19, 1))

    def test_final_scores_update_shared_records(self):
        self.game_rows[0].update(status="final", homePoints=75, awayPoints=70)
        self.sync()
        game = Game.objects.get()
        self.assertTrue(game.is_final)
        game.home_team.refresh_from_db()
        self.assertEqual(game.home_team.record_wins, 1)

    def test_dry_run_never_calls_provider(self):
        output = StringIO()
        call_command("sync_sport_data", sport=self.sport.pk, season=2027, start_date=self.start, end_date=self.end, dry_run=True, stdout=output)
        self.http.assert_not_called()
        self.assertFalse(Game.objects.exists())
        self.assertIn("No requests sent", output.getvalue())

    def test_unbounded_import_is_rejected_before_http(self):
        with self.assertRaises(CommandError):
            call_command("sync_sport_data", sport=self.sport.pk, season=2027)
        self.http.assert_not_called()

    def test_truncated_response_is_rejected(self):
        self.http.side_effect = None
        self.http.return_value = self.response([{}] * 3000)
        with self.assertRaises(ProviderError):
            BasketballProvider().games(2027, "start", "end")


class SportIntegrityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="integrity-user")
        self.football = Season.objects.create(year=2027, is_active=True)
        self.basketball = Season.objects.create(year=2027, sport_id="mens-college-basketball", is_active=True)
        self.league = League.objects.create(name="Integrity League", created_by=self.user)
        self.home = Team.objects.create(season=self.basketball, name="Home")
        self.away = Team.objects.create(season=self.basketball, name="Away")
        self.period = SchedulePeriod.objects.create(season=self.basketball, kind="slate", number=1, start_date=timezone.localdate(), end_date=timezone.localdate())
        self.game = Game.objects.create(season=self.basketball, schedule_period=self.period, home_team=self.home, away_team=self.away, start_time=timezone.now())

    def test_model_save_and_bulk_create_reject_cross_sport_rows(self):
        bad_rows = [
            Pick(league=self.league, game=self.game, user=self.user, picked_team=self.home),
            Ranking(season=self.football, team=self.home, rank=1, poll="AP"),
            TeamStat(season=self.football, team=self.home, stat="wins", value=1),
            MemberPeriod(league=self.league, schedule_period=self.period, user=self.user),
            MemberSeason(league=self.league, season=self.basketball, user=self.user),
            MemberSeasonPayment(league=self.league, season=self.basketball, user=self.user),
        ]
        for row in bad_rows:
            with self.subTest(model=type(row).__name__):
                with self.assertRaises(ValidationError):
                    row.save()
                with self.assertRaises(ValidationError):
                    type(row).objects.bulk_create([row])

    def test_queryset_update_rejects_invalid_relationship(self):
        team = Team.objects.create(season=self.football, name="Football")
        stat = TeamStat.objects.create(season=self.football, team=team, stat="wins", value=1)
        with self.assertRaises(ValidationError):
            TeamStat.objects.filter(pk=stat.pk).update(team=self.home)

    def test_only_one_active_season_per_sport(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Season.objects.create(year=2028, is_active=True)

    def test_schedule_period_requires_ordered_dates(self):
        with self.assertRaises(ValidationError):
            SchedulePeriod.objects.create(season=self.football, number=2, start_date=timezone.localdate(), end_date=timezone.localdate() - timedelta(days=1))


    def test_bulk_relationship_updates_validate_and_accept_valid_changes(self):
        one = Team.objects.create(season=self.football, name="First football team")
        two = Team.objects.create(season=self.football, name="Second football team")
        stat = TeamStat.objects.create(season=self.football, team=one, stat="wins", value=1)
        stat.team = two
        self.assertEqual(TeamStat.objects.bulk_update([stat], ["team"]), 1)
        stat.refresh_from_db()
        self.assertEqual(stat.team, two)
        stat.team = self.home
        with self.assertRaises(ValidationError):
            TeamStat.objects.bulk_update([stat], ["team"])
        stat.refresh_from_db()
        self.assertEqual(stat.team, two)


@override_settings(CFBD_API_KEY="fixture-only-key")
class BasketballGameplayIntegrationTests(TestCase):
    setUp = BasketballImportTests.setUp
    response = BasketballImportTests.response
    sync = BasketballImportTests.sync
    def test_imported_final_grades_pick_and_updates_period_standings(self):
        from cfb.models import LeagueMembership, LeagueGame
        self.sync()
        user = get_user_model().objects.create_user(username="basketball-picker")
        league = League.objects.create(name="Basketball integration", sport=self.sport, created_by=user)
        LeagueMembership.objects.create(league=league, user=user, role="owner")
        LeagueRules.objects.create(league=league, season=self.season, against_the_spread_enabled=False, points_per_correct_pick=3)
        game = Game.objects.get()
        LeagueGame.objects.create(league=league, game=game)
        pick = Pick.objects.create(league=league, game=game, user=user, picked_team=game.home_team)
        self.game_rows[0].update(status="final", homePoints=75, awayPoints=70)
        self.sync(force=True)
        pick.refresh_from_db()
        self.assertTrue(pick.is_correct)
        standing = MemberPeriod.objects.get(league=league, user=user, schedule_period=game.schedule_period)
        self.assertEqual(standing.points, 3)
        self.assertEqual(MemberSeason.objects.get(league=league, season=self.season, user=user).points, 3)

    def test_named_round_takes_priority_and_excludes_other_slates(self):
        from cfb.models import LeagueMembership, LeagueGame
        from cfb.services.schedule import get_current_period
        self.sync()
        game = Game.objects.get()
        original_day = game.schedule_period
        round_ = SchedulePeriod.objects.create(season=self.season, number=1, kind="round", name="Round of 64", season_type="postseason", start_date=date(2027, 3, 18), end_date=date(2027, 3, 21))
        self.sync(force=True)
        game.refresh_from_db()
        other = Game.objects.create(season=self.season, schedule_period=original_day, home_team=game.home_team, away_team=game.away_team, start_time=game.start_time)
        self.assertEqual(get_current_period(self.season, now=game.start_time), round_)
        user = get_user_model().objects.create_user(username="round-picker")
        league = League.objects.create(name="Round selection", sport=self.sport, created_by=user)
        LeagueMembership.objects.create(league=league, user=user, role="owner")
        LeagueGame.objects.create(league=league, game=game)
        LeagueGame.objects.create(league=league, game=other)
        with patch("django.utils.timezone.now", return_value=game.start_time - timedelta(minutes=5)):
            self.client.force_login(user)
            response = self.client.get("/picks/", {"league_id": league.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([selection.game_id for selection, pick in response.context["games_with_picks"]], [game.pk])
        self.assertContains(response, "Round of 64")

    @patch("cfb.services.reminders.user_notification_emails", return_value=["fixture@example.org"])
    @patch("cfb.services.reminders.send_mail")
    def test_reminder_uses_round_name_and_game_start(self, send_mail, emails):
        from cfb.services.reminders import send_pick_reminder_email
        self.sync()
        game = Game.objects.get()
        schedule_period = game.schedule_period
        schedule_period.name = "Sweet 16"
        schedule_period.kind = "round"
        schedule_period.save()
        user = get_user_model().objects.create_user(username="round-reminder")
        league = League.objects.create(name="Round reminders", sport=self.sport, created_by=user)
        status = {"picks_made": 0, "picks_required": 2, "missing_picks": 2, "key_picks_required": 0, "key_picks_made": 0, "missing_key_picks": 0}
        self.assertTrue(send_pick_reminder_email(user, league, schedule_period, game.start_time, status))
        self.assertIn("Sweet 16", send_mail.call_args.kwargs["subject"])
        body = send_mail.call_args.kwargs["message"]
        self.assertIn("Sweet 16", body)
        self.assertIn(timezone.localtime(game.start_time).strftime("%I:%M").lstrip("0"), body)
        self.assertNotIn("Week", body)
        self.assertNotIn("starts  at", body)
