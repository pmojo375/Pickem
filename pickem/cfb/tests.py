from decimal import Decimal
from unittest.mock import Mock, patch

from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from cfb.adapters import SocialAccountAdapter
from cfb.models import (
    Game,
    League,
    LeagueAnnouncement,
    LeagueGame,
    LeagueInvite,
    LeagueMembership,
    LeagueRules,
    MemberSeason,
    MemberSeasonPayment,
    MemberWeek,
    Pick,
    Season,
    Team,
    UserAnnouncementDismissal,
    Week,
)
from cfb.services import invites
from cfb.services.payouts import build_payout_summary
from cfb.services.scoring import (
    is_pick_correct,
    remaining_points_by_user,
    update_member_week_for_game,
)
from cfb.services.whatif import (
    WhatIfError,
    simulate_standings,
    synthesize_scores,
)
from cfb.templatetags.cfb_tags import apply_hooks, format_spread_display

User = get_user_model()


class ForcedHookTests(SimpleTestCase):
    def test_display_moves_whole_spreads_away_from_zero(self):
        self.assertEqual(format_spread_display(Decimal("3"), True), "3.5")
        self.assertEqual(format_spread_display(Decimal("-3"), True), "-3.5")
        self.assertEqual(apply_hooks(Decimal("0"), True), Decimal("0"))

    def test_display_leaves_existing_hooks_unchanged(self):
        self.assertEqual(format_spread_display(Decimal("3.5"), True), "3.5")
        self.assertEqual(format_spread_display(Decimal("-3.5"), True), "-3.5")

    def test_scoring_uses_same_negative_hook_shown_to_users(self):
        pick = Mock(league_id=1, picked_team_id=10)
        game = Mock(
            is_final=True,
            home_score=24,
            away_score=21,
            home_team_id=10,
        )
        rules = Mock(against_the_spread_enabled=True, force_hooks=True)
        league_game = Mock(locked_home_spread=Decimal("-3"))

        self.assertFalse(is_pick_correct(pick, game, rules, league_game))

    def test_scoring_uses_same_positive_hook_shown_to_users(self):
        pick = Mock(league_id=1, picked_team_id=10)
        game = Mock(
            is_final=True,
            home_score=21,
            away_score=24,
            home_team_id=10,
        )
        rules = Mock(against_the_spread_enabled=True, force_hooks=True)
        league_game = Mock(locked_home_spread=Decimal("3"))

        self.assertTrue(is_pick_correct(pick, game, rules, league_game))


class GamesListApiTests(TestCase):
    def setUp(self):
        self.season = Season.objects.create(year=2026, is_active=True)
        teams = [
            Team.objects.create(season=self.season, name=f"API Team {index}")
            for index in range(4)
        ]
        self.early_game = Game.objects.create(
            season=self.season,
            home_team=teams[0],
            away_team=teams[1],
            kickoff=timezone.now(),
        )
        self.late_game = Game.objects.create(
            season=self.season,
            home_team=teams[2],
            away_team=teams[3],
            kickoff=timezone.now() + timedelta(days=100),
        )

    def test_ids_filter_is_applied_before_ordered_result_limit(self):
        response = self.client.get(
            reverse("api_games_list"),
            {"ids": str(self.late_game.id), "limit": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [game["id"] for game in response.json()["games"]],
            [self.late_game.id],
        )

    def test_ids_filter_rejects_non_integer_values(self):
        response = self.client.get(reverse("api_games_list"), {"ids": "not-an-id"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid game IDs", response.json()["error"])


def _verify_email(user):
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"verified": True, "primary": True},
    )


class PickKeyPickLimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("picker", "picker@example.com", "pass")
        self.league = League.objects.create(name="Key Pick League", created_by=self.user)
        LeagueMembership.objects.create(
            league=self.league, user=self.user, role="owner"
        )
        self.season = Season.objects.create(year=2026, is_active=True)
        self.week = Week.objects.create(
            season=self.season,
            number=1,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=6),
        )
        LeagueRules.objects.create(
            league=self.league,
            season=self.season,
            key_picks_enabled=True,
            number_of_key_picks=1,
        )
        self.teams = [
            Team.objects.create(season=self.season, name=f"Team {index}")
            for index in range(4)
        ]
        self.games = []
        for index in range(2):
            game = Game.objects.create(
                season=self.season,
                week=self.week,
                home_team=self.teams[index * 2],
                away_team=self.teams[index * 2 + 1],
                kickoff=timezone.now() + timedelta(days=2),
            )
            LeagueGame.objects.create(league=self.league, game=game)
            self.games.append(game)
        self.client.force_login(self.user)

    def test_rejects_entire_submission_over_key_pick_limit(self):
        post_data = {"league_id": self.league.id}
        for game in self.games:
            post_data.update(
                {
                    f"game_{game.id}_id": game.id,
                    f"game_{game.id}_picked_team": game.home_team_id,
                    f"game_{game.id}_is_key_pick": "on",
                }
            )

        response = self.client.post(reverse("picks"), post_data)

        self.assertRedirects(
            response,
            f"/picks/?league_id={self.league.id}",
            fetch_redirect_response=False,
        )
        self.assertFalse(Pick.objects.filter(league=self.league, user=self.user).exists())
        response_messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(response_messages), 1)
        self.assertEqual(response_messages[0].level_tag, "error")
        self.assertIn("only select 1 key pick per week", str(response_messages[0]))

    def test_locked_key_pick_counts_toward_limit_when_checkbox_is_omitted(self):
        locked_game, new_game = self.games
        locked_game.kickoff = timezone.now() - timedelta(minutes=1)
        locked_game.save(update_fields=["kickoff"])
        Pick.objects.create(
            user=self.user,
            league=self.league,
            game=locked_game,
            picked_team=locked_game.home_team,
            is_key_pick=True,
        )

        response = self.client.post(
            reverse("picks"),
            {
                "league_id": self.league.id,
                f"game_{locked_game.id}_id": locked_game.id,
                f"game_{locked_game.id}_picked_team": locked_game.home_team_id,
                # Disabled inputs are omitted by browsers, and a crafted request
                # can omit this locked game's key-pick checkbox as well.
                f"game_{new_game.id}_id": new_game.id,
                f"game_{new_game.id}_picked_team": new_game.home_team_id,
                f"game_{new_game.id}_is_key_pick": "on",
            },
        )

        self.assertRedirects(
            response,
            f"/picks/?league_id={self.league.id}",
            fetch_redirect_response=False,
        )
        self.assertFalse(
            Pick.objects.filter(
                league=self.league, user=self.user, game=new_game
            ).exists()
        )
        self.assertTrue(
            Pick.objects.get(
                league=self.league, user=self.user, game=locked_game
            ).is_key_pick
        )
        response_messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(response_messages), 1)
        self.assertEqual(response_messages[0].level_tag, "error")
        self.assertIn("only select 1 key pick per week", str(response_messages[0]))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class LeagueEmailInviteTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.owner = User.objects.create_user("owner", "owner@example.com", "pass")
        self.league = League.objects.create(name="Test League", created_by=self.owner)
        LeagueMembership.objects.create(league=self.league, user=self.owner, role="owner")
        self.season = Season.objects.create(year=2026, is_active=True)
        self.request = self.factory.post("/leagues/1/email-invite/")
        self.request.user = self.owner

    def test_rejects_invalid_email(self):
        with self.assertRaises(ValidationError):
            invites.send_league_email_invite(self.request, self.league, "not-an-email")

    def test_existing_user_gets_personal_league_invite(self):
        User.objects.create_user("friend", "friend@example.com", "pass")
        result, email = invites.send_league_email_invite(
            self.request, self.league, "friend@example.com", season=self.season
        )
        self.assertEqual(result, "existing_sent")
        self.assertEqual(email, "friend@example.com")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("invited you to join", mail.outbox[0].body)
        self.assertIn("/invite/", mail.outbox[0].body)
        self.assertNotIn("/leagues/invite/", mail.outbox[0].body)
        self.assertNotIn("/accounts/signup/", mail.outbox[0].body)
        invite = LeagueInvite.objects.get(league=self.league, email="friend@example.com")
        self.assertTrue(invite.is_pending)

    def test_unknown_email_gets_personal_invite_link(self):
        result, email = invites.send_league_email_invite(
            self.request, self.league, "newbie@example.com", season=self.season
        )
        self.assertEqual(result, "new_sent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/invite/", mail.outbox[0].body)
        self.assertNotIn("/accounts/signup/", mail.outbox[0].body)
        self.assertNotIn("/leagues/invite/", mail.outbox[0].body)

    def test_inactive_member_gets_opt_in_email(self):
        member = User.objects.create_user("returner", "returner@example.com", "pass")
        LeagueMembership.objects.create(
            league=self.league, user=member, role="member", is_active=False
        )
        result, email = invites.send_league_email_invite(
            self.request, self.league, "returner@example.com", season=self.season
        )
        self.assertEqual(result, "opt_in_sent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("activate your membership", mail.outbox[0].body.lower())

    def test_active_member_is_not_emailed(self):
        member = User.objects.create_user("active", "active@example.com", "pass")
        LeagueMembership.objects.create(league=self.league, user=member, role="member")
        result, email = invites.send_league_email_invite(
            self.request, self.league, "active@example.com", season=self.season
        )
        self.assertEqual(result, "already_active")
        self.assertEqual(len(mail.outbox), 0)

    def test_bulk_send_generates_distinct_personal_invites(self):
        emails = ["alice@example.com", "bob@example.com", "carol@example.com"]
        results = invites.send_league_email_invites_bulk(
            self.request, self.league, emails, season=self.season
        )
        self.assertEqual(len(results), 3)
        tokens = set(
            LeagueInvite.objects.filter(league=self.league).values_list("token", flat=True)
        )
        self.assertEqual(len(tokens), 3)
        self.assertEqual(len(mail.outbox), 3)

    def test_resend_revokes_previous_pending_invite(self):
        invites.send_league_email_invite(
            self.request, self.league, "friend@example.com", season=self.season
        )
        first = LeagueInvite.objects.get(league=self.league, email="friend@example.com")
        first_token = first.token

        invites.send_league_email_invite(
            self.request, self.league, "friend@example.com", season=self.season
        )
        first.refresh_from_db()
        self.assertIsNotNone(first.revoked_at)

        active = LeagueInvite.objects.filter(
            league=self.league, email="friend@example.com", revoked_at__isnull=True
        ).get()
        self.assertNotEqual(active.token, first_token)
        self.assertTrue(active.is_pending)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PersonalInviteFlowTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.owner = User.objects.create_user("owner", "owner@example.com", "pass")
        self.league = League.objects.create(name="Invite League", created_by=self.owner)
        LeagueMembership.objects.create(league=self.league, user=self.owner, role="owner")
        self.invite = LeagueInvite.create_for_email(
            self.league, "invitee@example.com", invited_by=self.owner
        )
        self.invite_path = reverse("personal_invite", kwargs={"token": self.invite.token})

    def test_existing_user_accepts_matching_personal_invite(self):
        user = User.objects.create_user("invitee", "invitee@example.com", "pass")
        _verify_email(user)
        self.client.force_login(user)

        response = self.client.get(self.invite_path)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/leagues/{self.league.id}/")

        self.invite.refresh_from_db()
        self.assertIsNotNone(self.invite.accepted_at)
        membership = LeagueMembership.objects.get(league=self.league, user=user)
        self.assertTrue(membership.is_active)

    def test_new_user_accepts_invite_using_password_signup(self):
        response = self.client.post(
            reverse("personal_invite_signup", kwargs={"token": self.invite.token}),
            {
                "username": "newinvitee",
                "password1": "ComplexPass123!",
                "password2": "ComplexPass123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/leagues/{self.league.id}/")

        user = User.objects.get(username="newinvitee")
        self.assertEqual(user.email, "invitee@example.com")
        address = EmailAddress.objects.get(user=user)
        self.assertTrue(address.verified)
        self.invite.refresh_from_db()
        self.assertIsNotNone(self.invite.accepted_at)

    def test_existing_user_accepts_invite_using_google_without_duplicate_user(self):
        user = User.objects.create_user("googleuser", "invitee@example.com", "pass")
        _verify_email(user)
        self.client.force_login(user)

        response = self.client.get(self.invite_path)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.filter(email__iexact="invitee@example.com").count(), 1)

    def test_wrong_account_cannot_consume_invite(self):
        other = User.objects.create_user("other", "other@example.com", "pass")
        _verify_email(other)
        self.client.force_login(other)

        response = self.client.get(self.invite_path)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "invitee@example.com")
        self.assertContains(response, "other@example.com")
        self.invite.refresh_from_db()
        self.assertIsNone(self.invite.accepted_at)

    def _invite_session_request(self):
        session = self.client.session
        session[invites.PERSONAL_INVITE_TOKEN_SESSION_KEY] = self.invite.token
        session.save()
        request = self.factory.get(self.invite_path)
        request.session = session
        return request

    def test_google_sign_in_with_wrong_email_is_blocked(self):
        request = self._invite_session_request()
        sociallogin = Mock()
        sociallogin.user = Mock()
        sociallogin.user.email = "wrong@gmail.com"

        adapter = SocialAccountAdapter()
        with self.assertRaises(ImmediateHttpResponse) as raised:
            adapter.pre_social_login(request, sociallogin)

        response = raised.exception.response
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.invite_path)
        message_list = list(get_messages(request))
        self.assertEqual(len(message_list), 1)
        self.assertIn("invitee@example.com", str(message_list[0]))
        self.assertIn("wrong@gmail.com", str(message_list[0]))

    def test_google_sign_in_with_matching_email_is_allowed(self):
        request = self._invite_session_request()
        sociallogin = Mock()
        sociallogin.user = Mock()
        sociallogin.user.email = "invitee@example.com"

        adapter = SocialAccountAdapter()
        adapter.pre_social_login(request, sociallogin)

    @patch("cfb.adapters.DefaultSocialAccountAdapter.save_user")
    def test_save_user_does_not_sync_invite_email_on_mismatch(self, mock_super_save):
        user = User.objects.create_user("wronggoogle", "wrong@gmail.com", "pass")
        mock_super_save.return_value = user

        request = self._invite_session_request()
        sociallogin = Mock()
        sociallogin.user = user

        adapter = SocialAccountAdapter()
        result = adapter.save_user(request, sociallogin)

        self.assertEqual(result.email, "wrong@gmail.com")
        self.assertFalse(
            EmailAddress.objects.filter(
                user=user, email__iexact="invitee@example.com"
            ).exists()
        )

    def test_forwarded_invite_cannot_be_consumed_by_different_email(self):
        user = User.objects.create_user("stranger", "stranger@example.com", "pass")
        _verify_email(user)
        result = invites.accept_personal_invite(self.invite, user)
        self.assertEqual(result, "email_mismatch")

    def test_already_accepted_invite_handled_gracefully(self):
        user = User.objects.create_user("invitee", "invitee@example.com", "pass")
        _verify_email(user)
        self.invite.accepted_at = timezone.now()
        self.invite.save(update_fields=["accepted_at"])
        LeagueMembership.objects.create(league=self.league, user=user, role="member")
        self.client.force_login(user)

        response = self.client.get(self.invite_path)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/leagues/{self.league.id}/")

    def test_expired_invite_rejected(self):
        self.invite.expires_at = timezone.now() - timedelta(days=1)
        self.invite.save(update_fields=["expires_at"])

        response = self.client.get(self.invite_path)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/leagues/")

    def test_revoked_invite_rejected(self):
        self.invite.revoke()

        response = self.client.get(self.invite_path)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/leagues/")

    def test_inactive_membership_reactivated(self):
        user = User.objects.create_user("invitee", "invitee@example.com", "pass")
        _verify_email(user)
        membership = LeagueMembership.objects.create(
            league=self.league, user=user, role="member", is_active=False
        )
        self.client.force_login(user)

        response = self.client.get(self.invite_path)
        self.assertEqual(response.status_code, 302)
        membership.refresh_from_db()
        self.assertTrue(membership.is_active)

    def test_active_member_gets_sensible_behavior(self):
        user = User.objects.create_user("invitee", "invitee@example.com", "pass")
        _verify_email(user)
        LeagueMembership.objects.create(league=self.league, user=user, role="member", is_active=True)
        self.client.force_login(user)

        response = self.client.get(self.invite_path)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/leagues/{self.league.id}/")
        self.invite.refresh_from_db()
        self.assertIsNotNone(self.invite.accepted_at)

    def test_unauthenticated_user_sees_auth_options(self):
        response = self.client.get(self.invite_path)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Continue with Google")
        self.assertContains(response, "Create account with email and password")
        self.assertContains(response, "invitee@example.com")

    def test_preentered_user_sees_set_password_option(self):
        user = User(username="preentered", email="invitee@example.com")
        user.set_unusable_password()
        user.save()

        response = self.client.get(self.invite_path)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Continue with Google")
        self.assertContains(response, "Set your password")
        self.assertNotContains(response, "Create account with email and password")
        self.assertNotContains(response, "Already have an account?")

    def test_preentered_user_can_set_password_and_join(self):
        user = User(username="preentered", email="invitee@example.com")
        user.set_unusable_password()
        user.save()

        response = self.client.post(
            reverse("personal_invite_set_password", kwargs={"token": self.invite.token}),
            {
                "password1": "ComplexPass123!",
                "password2": "ComplexPass123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/leagues/{self.league.id}/")

        user.refresh_from_db()
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.check_password("ComplexPass123!"))
        address = EmailAddress.objects.get(user=user)
        self.assertTrue(address.verified)
        self.invite.refresh_from_db()
        self.assertIsNotNone(self.invite.accepted_at)

    def test_preentered_user_signup_redirects_to_set_password(self):
        user = User(username="preentered", email="invitee@example.com")
        user.set_unusable_password()
        user.save()

        response = self.client.get(
            reverse("personal_invite_signup", kwargs={"token": self.invite.token})
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("personal_invite_set_password", kwargs={"token": self.invite.token}),
        )


class GenericLeagueInviteTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", "owner@example.com", "pass")
        self.league = League.objects.create(name="Generic League", created_by=self.owner)
        LeagueMembership.objects.create(league=self.league, user=self.owner, role="owner")
        self.token = self.league.get_invite_token()

    def test_generic_league_invite_still_works(self):
        user = User.objects.create_user("joiner", "joiner@example.com", "pass")
        _verify_email(user)
        self.client.force_login(user)

        path = reverse("league_invite", kwargs={"token": self.token})
        confirm = self.client.get(path)
        self.assertEqual(confirm.status_code, 200)

        response = self.client.post(path)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            LeagueMembership.objects.filter(league=self.league, user=user, is_active=True).exists()
        )


class OrdinarySignupEmailVerificationTests(TestCase):
    def test_ordinary_signup_still_requires_email_verification(self):
        response = self.client.post(
            "/accounts/signup/",
            {
                "username": "regular",
                "email": "regular@example.com",
                "password1": "ComplexPass123!",
                "password2": "ComplexPass123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/confirm-email/", response["Location"])

        address = EmailAddress.objects.get(email="regular@example.com")
        self.assertFalse(address.verified)


class PayoutSummaryTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", "owner@example.com", "pass")
        self.league = League.objects.create(name="Payout League", created_by=self.owner)
        LeagueMembership.objects.create(league=self.league, user=self.owner, role="owner")
        self.season = Season.objects.create(year=2026, is_active=True)
        self.rules = LeagueRules.objects.create(
            league=self.league,
            season=self.season,
            entry_fee=Decimal("50.00"),
            weekly_payout_percent=Decimal("40.00"),
            season_payout_percent=Decimal("60.00"),
            weekly_payout_structure={"1": 100},
            season_payout_structure={"1": 70, "2": 20},
            season_payout_last_percent=Decimal("10.00"),
        )

    def test_entry_and_place_payouts(self):
        summary = build_payout_summary(self.rules, member_count=10)
        self.assertEqual(summary["entry_fee"], Decimal("50.00"))
        self.assertEqual(summary["total_pool"], Decimal("500.00"))
        self.assertEqual(summary["weekly_places"][0]["label"], "1st place")
        self.assertEqual(summary["weekly_places"][0]["amount"], Decimal("16.67"))
        self.assertEqual(summary["season_places"][0]["label"], "1st place")
        self.assertEqual(summary["season_places"][0]["amount"], Decimal("210.00"))
        self.assertEqual(summary["season_places"][1]["amount"], Decimal("60.00"))
        self.assertEqual(summary["last_place"]["amount"], Decimal("30.00"))

    def test_no_payout_returns_none(self):
        self.rules.entry_fee = Decimal("0.00")
        self.rules.weekly_payout_percent = Decimal("0.00")
        self.rules.season_payout_percent = Decimal("0.00")
        self.assertIsNone(build_payout_summary(self.rules, member_count=10))

    def test_last_place_goes_to_most_incorrect_not_worst_rank(self):
        from cfb.services.payouts import attach_prize_amounts

        summary = build_payout_summary(self.rules, member_count=10)
        # Only 1st is paid from places; last place uses most incorrect among unpaid.
        standings = [
            {"user_id": 1, "display_rank": 1, "incorrect": 2},
            {"user_id": 2, "display_rank": 2, "incorrect": 8},
            {"user_id": 3, "display_rank": 3, "incorrect": 0},  # worst rank, skipped picks
        ]
        attach_prize_amounts(
            standings,
            [{"place": 1, "label": "1st place", "amount": summary["season_places"][0]["amount"]}],
            summary["last_place"],
        )
        by_user = {row["user_id"]: row for row in standings}
        self.assertEqual(by_user[1]["prize_label"], "1st place")
        self.assertEqual(by_user[2]["prize_label"], "Last place")
        self.assertEqual(by_user[2]["prize_amount"], Decimal("30.00"))
        self.assertIsNone(by_user[3]["prize_amount"])

    def test_last_place_ties_split_on_incorrect(self):
        from cfb.services.payouts import attach_prize_amounts

        summary = build_payout_summary(self.rules, member_count=10)
        standings = [
            {"user_id": 1, "display_rank": 1, "incorrect": 1},
            {"user_id": 2, "display_rank": 2, "incorrect": 5},
            {"user_id": 3, "display_rank": 3, "incorrect": 5},
        ]
        attach_prize_amounts(
            standings,
            [{"place": 1, "label": "1st place", "amount": summary["season_places"][0]["amount"]}],
            summary["last_place"],
        )
        by_user = {row["user_id"]: row for row in standings}
        self.assertEqual(by_user[2]["prize_label"], "Last place (tie)")
        self.assertEqual(by_user[3]["prize_label"], "Last place (tie)")
        self.assertEqual(by_user[2]["prize_amount"], Decimal("15.00"))
        self.assertEqual(by_user[3]["prize_amount"], Decimal("15.00"))


class MemberRulesViewTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", "owner@example.com", "pass")
        self.member = User.objects.create_user("member", "member@example.com", "pass")
        self.league = League.objects.create(name="Member League", created_by=self.owner)
        LeagueMembership.objects.create(league=self.league, user=self.owner, role="owner")
        LeagueMembership.objects.create(league=self.league, user=self.member, role="member")
        self.season = Season.objects.create(year=2026, is_active=True)
        LeagueRules.objects.create(
            league=self.league,
            season=self.season,
            entry_fee=Decimal("25.00"),
            weekly_payout_percent=Decimal("50.00"),
            season_payout_percent=Decimal("50.00"),
            weekly_payout_structure={"1": 100},
            season_payout_structure={"1": 100},
        )

    def test_member_sees_rules_on_league_detail(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/leagues/{self.league.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Entry cost")
        self.assertContains(response, "$25.00")
        self.assertContains(response, "1st place")
        self.assertContains(response, "Each week")
        self.assertContains(response, "Season finish")

    def test_member_sees_readonly_invite_link(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/leagues/{self.league.id}/")
        self.assertContains(response, "Invite people")
        self.assertContains(response, self.league.get_invite_path())
        self.assertNotContains(response, "Regenerate invite link")
        self.assertNotContains(response, "Invite by email")
        self.assertNotContains(response, "Change join password")

    def test_admin_sees_invite_management_controls(self):
        self.client.force_login(self.owner)
        response = self.client.get(f"/leagues/{self.league.id}/")
        self.assertContains(response, "Regenerate invite link")
        self.assertContains(response, "Invite by email")
        self.assertContains(response, "Change join password")


class ReturningMemberLoginTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user("returner", "returner@example.com", "pass")
        self.owner = User.objects.create_user("owner2", "owner2@example.com", "pass")
        self.league = League.objects.create(name="Returning League", created_by=self.owner)
        LeagueMembership.objects.create(league=self.league, user=self.owner, role="owner")
        self.membership = LeagueMembership.objects.create(
            league=self.league, user=self.member, role="member", is_active=False
        )
        self.season = Season.objects.create(year=2026, is_active=True)
        self.league.is_active = True
        self.league.season_opt_in_required = False
        self.league.save(update_fields=["is_active", "season_opt_in_required"])

    def test_opt_in_login_keeps_next_for_password_and_google(self):
        token = self.membership.get_opt_in_token(self.season.year)
        opt_in_path = f"/leagues/opt-in/{token}/"

        response = self.client.get(opt_in_path)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
        self.assertIn("next=", response["Location"])
        self.assertIn("opt-in", response["Location"])

        login_page = self.client.get(response["Location"])
        self.assertEqual(login_page.status_code, 200)
        self.assertContains(login_page, f'name="next"')
        self.assertContains(login_page, opt_in_path)
        self.assertContains(login_page, "next=")
        self.assertContains(login_page, "google")

    def test_password_login_returns_to_opt_in(self):
        from allauth.account.models import EmailAddress

        EmailAddress.objects.create(
            user=self.member,
            email=self.member.email,
            verified=True,
            primary=True,
        )
        token = self.membership.get_opt_in_token(self.season.year)
        opt_in_path = f"/leagues/opt-in/{token}/"

        response = self.client.post(
            "/accounts/login/",
            {
                "login": self.member.username,
                "password": "pass",
                "next": opt_in_path,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], opt_in_path)

        confirm = self.client.get(opt_in_path)
        self.assertEqual(confirm.status_code, 200)
        self.assertContains(confirm, "Activate my membership")


class SyncVerifiedEmailsCommandTests(TestCase):
    def test_creates_and_verifies_missing_email_address(self):
        from django.core.management import call_command
        from allauth.account.models import EmailAddress

        user = User.objects.create_user("imported", "imported@example.com", "pass")
        self.assertFalse(EmailAddress.objects.filter(user=user).exists())

        call_command("sync_verified_emails", commit=True)

        address = EmailAddress.objects.get(user=user, email="imported@example.com")
        self.assertTrue(address.verified)
        self.assertTrue(address.primary)


class AccountProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("oldname", "user@example.com", "pass")
        _verify_email(self.user)
        self.client.force_login(self.user)
        User.objects.create_user("taken", "other@example.com", "pass")

    def test_user_can_change_username(self):
        response = self.client.post(
            reverse("account"),
            {
                "action": "update_name",
                "username": "newname",
                "first_name": "Pat",
                "last_name": "Mojo",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "newname")
        self.assertEqual(self.user.first_name, "Pat")
        self.assertEqual(self.user.last_name, "Mojo")

    def test_username_normalized_to_lowercase(self):
        response = self.client.post(
            reverse("account"),
            {
                "action": "update_name",
                "username": "NewName",
                "first_name": "",
                "last_name": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "newname")

    def test_cannot_take_existing_username(self):
        response = self.client.post(
            reverse("account"),
            {
                "action": "update_name",
                "username": "taken",
                "first_name": "",
                "last_name": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That username is already taken.")
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "oldname")

    def test_unchanged_username_is_allowed(self):
        response = self.client.post(
            reverse("account"),
            {
                "action": "update_name",
                "username": "oldname",
                "first_name": "Pat",
                "last_name": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "oldname")
        self.assertEqual(self.user.first_name, "Pat")

    def test_login_works_with_new_username(self):
        from django.contrib.auth import authenticate

        self.client.post(
            reverse("account"),
            {
                "action": "update_name",
                "username": "newname",
                "first_name": "",
                "last_name": "",
            },
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "newname")
        self.assertIsNotNone(authenticate(username="newname", password="pass"))
        self.assertIsNone(authenticate(username="oldname", password="pass"))

    def test_account_shows_create_password_without_usable_password(self):
        self.user.set_unusable_password()
        self.user.save()
        self.client.force_login(self.user)

        response = self.client.get(reverse("account"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create password")
        self.assertNotContains(response, "Change password")
        self.assertNotContains(response, "Current password")

    def test_user_can_create_password_from_account_page(self):
        self.user.set_unusable_password()
        self.user.save()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("account"),
            {
                "action": "change_password",
                "password1": "NewComplexPass123!",
                "password2": "NewComplexPass123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewComplexPass123!"))


class LeagueAnnouncementTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="ann_owner", password="pass")
        self.member = User.objects.create_user(username="ann_member", password="pass")
        self.outsider = User.objects.create_user(username="ann_out", password="pass")
        self.league = League.objects.create(name="Announce League", created_by=self.owner)
        LeagueMembership.objects.create(league=self.league, user=self.owner, role="owner")
        LeagueMembership.objects.create(league=self.league, user=self.member, role="member")

    def test_manager_can_create_announcement(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("settings") + f"?league_id={self.league.id}",
            {
                "do": "create_announcement",
                "league_id": self.league.id,
                "title": "Welcome",
                "body": "Season starts soon",
                "kind": "one_time",
                "level": "info",
            },
        )
        self.assertEqual(response.status_code, 302)
        announcement = LeagueAnnouncement.objects.get(league=self.league)
        self.assertEqual(announcement.title, "Welcome")
        self.assertEqual(announcement.created_by, self.owner)

    def test_create_with_email_sends_to_active_members(self):
        self.owner.email = "owner@example.com"
        self.owner.save(update_fields=["email"])
        self.member.email = "member@example.com"
        self.member.save(update_fields=["email"])
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("settings") + f"?league_id={self.league.id}",
            {
                "do": "create_announcement",
                "league_id": self.league.id,
                "title": "Dues due",
                "body": "Pay by Friday",
                "kind": "one_time",
                "level": "warning",
                "send_email": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)
        subjects = {email.subject for email in mail.outbox}
        self.assertEqual(subjects, {"Announce League: Dues due"})
        bodies = " ".join(email.body for email in mail.outbox)
        self.assertIn("Pay by Friday", bodies)

    def test_member_sees_announcement_on_home(self):
        LeagueAnnouncement.objects.create(
            league=self.league,
            title="Pay dues",
            body="Please pay by Friday",
            kind=LeagueAnnouncement.KIND_PERSISTENT,
            created_by=self.owner,
        )
        self.client.force_login(self.member)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Pay dues")
        self.assertContains(response, "Please pay by Friday")

    def test_outsider_does_not_see_announcement(self):
        LeagueAnnouncement.objects.create(
            league=self.league,
            title="Members only",
            body="Secret",
            kind=LeagueAnnouncement.KIND_ONE_TIME,
            created_by=self.owner,
        )
        self.client.force_login(self.outsider)
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "Members only")

    def test_one_time_dismiss_hides_for_user(self):
        announcement = LeagueAnnouncement.objects.create(
            league=self.league,
            title="Dismiss me",
            body="Once",
            kind=LeagueAnnouncement.KIND_ONE_TIME,
            created_by=self.owner,
        )
        self.client.force_login(self.member)
        response = self.client.post(reverse("announcement_dismiss", args=[announcement.id]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            UserAnnouncementDismissal.objects.filter(
                announcement=announcement, user=self.member
            ).exists()
        )
        home = self.client.get(reverse("home"))
        self.assertNotContains(home, "Dismiss me")

    def test_persistent_cannot_be_dismissed(self):
        announcement = LeagueAnnouncement.objects.create(
            league=self.league,
            title="Always here",
            body="Stay",
            kind=LeagueAnnouncement.KIND_PERSISTENT,
            created_by=self.owner,
        )
        self.client.force_login(self.member)
        response = self.client.post(reverse("announcement_dismiss", args=[announcement.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            UserAnnouncementDismissal.objects.filter(
                announcement=announcement, user=self.member
            ).exists()
        )
        home = self.client.get(reverse("home"))
        self.assertContains(home, "Always here")

    def test_inactive_announcement_hidden(self):
        LeagueAnnouncement.objects.create(
            league=self.league,
            title="Off",
            body="Hidden",
            kind=LeagueAnnouncement.KIND_PERSISTENT,
            is_active=False,
            created_by=self.owner,
        )
        self.client.force_login(self.member)
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "Hidden")

    def test_member_cannot_create_announcement(self):
        self.client.force_login(self.member)
        response = self.client.post(
            reverse("settings"),
            {
                "do": "create_announcement",
                "league_id": self.league.id,
                "title": "Nope",
                "body": "Denied",
                "kind": "one_time",
                "level": "info",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LeagueAnnouncement.objects.filter(title="Nope").exists())


class EntryFeeReceiptDismissTests(TestCase):
    def setUp(self):
        self.season = Season.objects.create(year=2026, is_active=True)
        self.owner = User.objects.create_user(username="fee_owner", password="pass")
        self.member = User.objects.create_user(username="fee_member", password="pass")
        self.league = League.objects.create(name="Fee League", created_by=self.owner)
        LeagueMembership.objects.create(league=self.league, user=self.owner, role="owner")
        LeagueMembership.objects.create(league=self.league, user=self.member, role="member")
        LeagueRules.objects.create(
            league=self.league,
            season=self.season,
            entry_fee=Decimal("25.00"),
        )

    def test_unpaid_alert_stays_visible(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "entry fee due")
        self.assertContains(response, "Fee League")

    def test_paid_receipt_can_be_dismissed(self):
        MemberSeasonPayment.objects.create(
            league=self.league,
            season=self.season,
            user=self.member,
            paid=True,
        )
        self.client.force_login(self.member)
        home = self.client.get(reverse("home"))
        self.assertContains(home, "Entry fee received")

        response = self.client.post(
            reverse("entry_fee_receipt_dismiss", args=[self.league.id])
        )
        self.assertEqual(response.status_code, 302)
        payment = MemberSeasonPayment.objects.get(
            league=self.league, season=self.season, user=self.member
        )
        self.assertTrue(payment.paid_receipt_dismissed)

        home = self.client.get(reverse("home"))
        self.assertNotContains(home, "Entry fee received")

    def test_marking_paid_again_reshows_receipt(self):
        payment = MemberSeasonPayment.objects.create(
            league=self.league,
            season=self.season,
            user=self.member,
            paid=True,
            paid_receipt_dismissed=True,
        )
        membership = LeagueMembership.objects.get(league=self.league, user=self.member)
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("league_member_paid", args=[self.league.id, membership.id]),
            {"paid": "paid"},
        )
        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        self.assertTrue(payment.paid)
        self.assertFalse(payment.paid_receipt_dismissed)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SUPPORT_EMAIL="support@bigpicks.app",
    DEFAULT_FROM_EMAIL="noreply@bigpicks.app",
)
class ContactFormTests(TestCase):
    def test_contact_page_shows_support_address_when_anonymous(self):
        response = self.client.get(reverse("contact"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "support@bigpicks.app")
        self.assertContains(response, 'href="mailto:support@bigpicks.app"')
        self.assertContains(response, "Sign in to contact us")
        self.assertNotContains(response, "Send message")

    def test_footer_links_to_contact(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, reverse("contact"))
        self.assertContains(response, "Contact")

    def test_anonymous_submit_is_rejected(self):
        response = self.client.post(
            reverse("contact"),
            {
                "name": "Alex Fan",
                "email": "alex@example.com",
                "subject": "Help with picks",
                "message": "I cannot save my picks.",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response["Location"])
        self.assertEqual(len(mail.outbox), 0)

    def test_logged_in_submit_sends_support_and_confirmation(self):
        user = User.objects.create_user(
            "contactuser",
            "contactuser@example.com",
            "pass",
            first_name="Casey",
            last_name="Pick",
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse("contact"),
            {
                "name": "Casey Pick",
                "email": "contactuser@example.com",
                "subject": "Billing question",
                "message": "Where do I pay?",
            },
        )
        self.assertRedirects(response, f"{reverse('contact')}?sent=1")
        self.assertEqual(len(mail.outbox), 2)

        support_msg = mail.outbox[0]
        self.assertEqual(support_msg.to, ["support@bigpicks.app"])
        self.assertEqual(support_msg.from_email, "noreply@bigpicks.app")
        self.assertEqual(support_msg.reply_to, ["contactuser@example.com"])
        self.assertIn("[BigPicks Contact] Billing question", support_msg.subject)
        self.assertIn("Casey Pick", support_msg.body)
        self.assertIn("contactuser", support_msg.body)
        self.assertIn(f"id {user.pk}", support_msg.body)
        self.assertIn("Where do I pay?", support_msg.body)

        confirm_msg = mail.outbox[1]
        self.assertEqual(confirm_msg.to, ["contactuser@example.com"])
        self.assertIn("We received your message", confirm_msg.subject)

        success_page = self.client.get(f"{reverse('contact')}?sent=1")
        self.assertContains(success_page, "Message sent")
        self.assertContains(success_page, "support received your message")

    def test_invalid_form_does_not_send_mail(self):
        user = User.objects.create_user("badform", "badform@example.com", "pass")
        self.client.force_login(user)
        response = self.client.post(
            reverse("contact"),
            {
                "name": "",
                "email": "not-an-email",
                "subject": "",
                "message": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)


class RemainingPointsByUserTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("possuser", "poss@example.com", "pass")
        self.league = League.objects.create(name="Points Possible League", created_by=self.user)
        LeagueMembership.objects.create(league=self.league, user=self.user, role="owner")
        self.season = Season.objects.create(year=2026, is_active=True)
        self.week = Week.objects.create(
            season=self.season,
            number=1,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=6),
        )
        self.rules = LeagueRules.objects.create(
            league=self.league,
            season=self.season,
            points_per_correct_pick=1,
            key_pick_extra_points=1,
            key_picks_enabled=True,
        )
        teams = [
            Team.objects.create(season=self.season, name=f"Poss Team {i}")
            for i in range(4)
        ]
        self.open_game = Game.objects.create(
            season=self.season,
            week=self.week,
            home_team=teams[0],
            away_team=teams[1],
            kickoff=timezone.now() + timedelta(days=1),
            is_final=False,
        )
        self.final_game = Game.objects.create(
            season=self.season,
            week=self.week,
            home_team=teams[2],
            away_team=teams[3],
            kickoff=timezone.now() - timedelta(days=1),
            is_final=True,
            home_score=21,
            away_score=14,
        )
        LeagueGame.objects.create(league=self.league, game=self.open_game)
        LeagueGame.objects.create(league=self.league, game=self.final_game)

    def test_counts_key_pick_bonus_only_for_unfinished_games(self):
        Pick.objects.create(
            user=self.user,
            league=self.league,
            game=self.open_game,
            picked_team=self.open_game.home_team,
            is_key_pick=True,
        )
        Pick.objects.create(
            user=self.user,
            league=self.league,
            game=self.final_game,
            picked_team=self.final_game.home_team,
            is_key_pick=False,
            is_correct=True,
        )

        remaining = remaining_points_by_user(
            self.league, self.rules, week=self.week
        )
        self.assertEqual(remaining, {self.user.id: 2})


class InactiveLeagueGameScoringTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("inactivepick", "inactive@example.com", "pass")
        self.league = League.objects.create(name="Inactive Slate League", created_by=self.user)
        LeagueMembership.objects.create(league=self.league, user=self.user, role="owner")
        self.season = Season.objects.create(year=2026, is_active=True)
        self.week = Week.objects.create(
            season=self.season,
            number=1,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=6),
        )
        LeagueRules.objects.create(
            league=self.league,
            season=self.season,
            against_the_spread_enabled=True,
            force_hooks=True,
            points_per_correct_pick=1,
        )
        teams = [
            Team.objects.create(season=self.season, name=f"Inactive Team {i}")
            for i in range(4)
        ]
        self.active_game = Game.objects.create(
            season=self.season,
            week=self.week,
            home_team=teams[0],
            away_team=teams[1],
            kickoff=timezone.now() - timedelta(hours=3),
            is_final=True,
            home_score=28,
            away_score=21,
        )
        self.removed_game = Game.objects.create(
            season=self.season,
            week=self.week,
            home_team=teams[2],
            away_team=teams[3],
            kickoff=timezone.now() - timedelta(hours=1),
            is_final=True,
            home_score=17,
            away_score=14,
        )
        LeagueGame.objects.create(
            league=self.league,
            game=self.active_game,
            locked_home_spread=Decimal("-3.5"),
            locked_away_spread=Decimal("3.5"),
            is_active=True,
        )
        LeagueGame.objects.create(
            league=self.league,
            game=self.removed_game,
            locked_home_spread=Decimal("-7"),
            locked_away_spread=Decimal("7"),
            is_active=False,
        )
        Pick.objects.create(
            user=self.user,
            league=self.league,
            game=self.active_game,
            picked_team=self.active_game.home_team,
            is_correct=None,
        )
        Pick.objects.create(
            user=self.user,
            league=self.league,
            game=self.removed_game,
            picked_team=self.removed_game.home_team,
            is_correct=None,
        )

    def test_week_stats_ignore_picks_on_inactive_league_games(self):
        update_member_week_for_game(self.active_game)

        member_week = MemberWeek.objects.get(
            league=self.league, week=self.week, user=self.user
        )
        self.assertEqual(member_week.picks_made, 1)
        self.assertEqual(member_week.correct, 1)
        self.assertEqual(member_week.incorrect, 0)
        self.assertEqual(member_week.ties, 0)
        self.assertEqual(member_week.points, 1)


class SynthesizeScoresTests(SimpleTestCase):
    def test_straight_up_winners(self):
        self.assertEqual(
            synthesize_scores("home", against_the_spread=False, locked_home_spread=None, force_hooks=False),
            (1, 0),
        )
        self.assertEqual(
            synthesize_scores("away", against_the_spread=False, locked_home_spread=None, force_hooks=False),
            (0, 1),
        )

    def test_ats_home_and_away_cover(self):
        home_h, home_a = synthesize_scores(
            "home",
            against_the_spread=True,
            locked_home_spread=Decimal("-7"),
            force_hooks=False,
        )
        # Home -7 covers when margin > 7
        self.assertGreater(home_h - home_a, 7)

        away_h, away_a = synthesize_scores(
            "away",
            against_the_spread=True,
            locked_home_spread=Decimal("-7"),
            force_hooks=False,
        )
        self.assertLess(away_h - away_a, 7)

    def test_ats_push_on_whole_spread(self):
        h, a = synthesize_scores(
            "push",
            against_the_spread=True,
            locked_home_spread=Decimal("-3"),
            force_hooks=False,
        )
        self.assertEqual(h - a, 3)

    def test_push_rejected_with_hooks_or_half_point(self):
        with self.assertRaises(WhatIfError):
            synthesize_scores(
                "push",
                against_the_spread=True,
                locked_home_spread=Decimal("-3"),
                force_hooks=True,
            )
        with self.assertRaises(WhatIfError):
            synthesize_scores(
                "push",
                against_the_spread=True,
                locked_home_spread=Decimal("-3.5"),
                force_hooks=False,
            )


class WhatIfStandingsTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user("whatif_a", "a@example.com", "pass")
        self.user_b = User.objects.create_user("whatif_b", "b@example.com", "pass")
        self.league = League.objects.create(name="What If League", created_by=self.user_a)
        LeagueMembership.objects.create(league=self.league, user=self.user_a, role="owner")
        LeagueMembership.objects.create(league=self.league, user=self.user_b, role="member")
        self.season = Season.objects.create(year=2026, is_active=True)
        self.week1 = Week.objects.create(
            season=self.season,
            number=1,
            start_date=timezone.localdate() - timedelta(days=14),
            end_date=timezone.localdate() - timedelta(days=8),
        )
        self.week2 = Week.objects.create(
            season=self.season,
            number=2,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=6),
        )
        self.rules = LeagueRules.objects.create(
            league=self.league,
            season=self.season,
            against_the_spread_enabled=True,
            force_hooks=False,
            points_per_correct_pick=1,
            key_pick_extra_points=1,
            key_picks_enabled=True,
            drop_weeks=1,
        )
        teams = [
            Team.objects.create(season=self.season, name=f"WhatIf Team {i}")
            for i in range(6)
        ]
        # Week 1 final game (already graded into MemberWeek below)
        self.w1_game = Game.objects.create(
            season=self.season,
            week=self.week1,
            home_team=teams[0],
            away_team=teams[1],
            kickoff=timezone.now() - timedelta(days=10),
            is_final=True,
            home_score=24,
            away_score=17,
        )
        LeagueGame.objects.create(
            league=self.league,
            game=self.w1_game,
            locked_home_spread=Decimal("-3"),
            locked_away_spread=Decimal("3"),
            is_active=True,
        )
        # Week 2: one final, one started, one unstarted
        self.w2_final = Game.objects.create(
            season=self.season,
            week=self.week2,
            home_team=teams[2],
            away_team=teams[3],
            kickoff=timezone.now() - timedelta(hours=6),
            is_final=True,
            home_score=21,
            away_score=14,
        )
        self.w2_live = Game.objects.create(
            season=self.season,
            week=self.week2,
            home_team=teams[4],
            away_team=teams[5],
            kickoff=timezone.now() - timedelta(hours=1),
            is_final=False,
            home_score=10,
            away_score=7,
        )
        self.w2_upcoming = Game.objects.create(
            season=self.season,
            week=self.week2,
            home_team=teams[0],
            away_team=teams[2],
            kickoff=timezone.now() + timedelta(days=1),
            is_final=False,
        )
        self.lg_final = LeagueGame.objects.create(
            league=self.league,
            game=self.w2_final,
            locked_home_spread=Decimal("-3.5"),
            locked_away_spread=Decimal("3.5"),
            is_active=True,
        )
        self.lg_live = LeagueGame.objects.create(
            league=self.league,
            game=self.w2_live,
            locked_home_spread=Decimal("-7"),
            locked_away_spread=Decimal("7"),
            is_active=True,
        )
        LeagueGame.objects.create(
            league=self.league,
            game=self.w2_upcoming,
            locked_home_spread=Decimal("-1"),
            locked_away_spread=Decimal("1"),
            is_active=True,
        )

        # Picks: A picks home on all; B picks away on live + final week2
        for game, team_a, team_b in [
            (self.w1_game, self.w1_game.home_team, self.w1_game.away_team),
            (self.w2_final, self.w2_final.home_team, self.w2_final.away_team),
            (self.w2_live, self.w2_live.home_team, self.w2_live.away_team),
            (self.w2_upcoming, self.w2_upcoming.home_team, self.w2_upcoming.away_team),
        ]:
            Pick.objects.create(
                user=self.user_a, league=self.league, game=game, picked_team=team_a
            )
            Pick.objects.create(
                user=self.user_b, league=self.league, game=game, picked_team=team_b
            )

        # Persist official week1 + week2 partial stats
        update_member_week_for_game(self.w1_game)
        update_member_week_for_game(self.w2_final)

    def test_hypo_week_grades_simulated_and_final_games(self):
        # Home covers on live (-7): A picked home → correct; B picked away → incorrect
        result = simulate_standings(
            self.league,
            self.week2,
            self.rules,
            {self.w2_live.id: "home"},
        )
        by_user = {row["user_id"]: row for row in result.week_standings}
        # Final: home covered -3.5 (margin 7) → A correct, B incorrect
        # Live hypo: home covers → A correct, B incorrect
        self.assertEqual(by_user[self.user_a.id]["wins"], 2)
        self.assertEqual(by_user[self.user_a.id]["points"], 2)
        self.assertEqual(by_user[self.user_b.id]["wins"], 0)
        self.assertEqual(by_user[self.user_b.id]["losses"], 2)
        self.assertEqual(by_user[self.user_a.id]["hypo_rank"], 1)
        self.assertEqual(by_user[self.user_b.id]["hypo_rank"], 2)

    def test_unset_games_award_no_points(self):
        result = simulate_standings(
            self.league, self.week2, self.rules, {}
        )
        by_user = {row["user_id"]: row for row in result.week_standings}
        # Only the final week2 game counts
        self.assertEqual(by_user[self.user_a.id]["wins"], 1)
        self.assertEqual(by_user[self.user_a.id]["picks_made"], 1)

    def test_rejects_unstarted_game(self):
        with self.assertRaises(WhatIfError):
            simulate_standings(
                self.league,
                self.week2,
                self.rules,
                {self.w2_upcoming.id: "home"},
            )

    def test_does_not_mutate_persisted_stats(self):
        mw_before = list(
            MemberWeek.objects.filter(league=self.league).values_list(
                "id", "points", "correct", "rank"
            )
        )
        ms_before = list(
            MemberSeason.objects.filter(league=self.league).values_list(
                "id", "points", "correct", "rank"
            )
        )
        picks_before = list(Pick.objects.filter(league=self.league).values_list("id", "is_correct"))

        simulate_standings(
            self.league,
            self.week2,
            self.rules,
            {self.w2_live.id: "away"},
        )

        mw_after = list(
            MemberWeek.objects.filter(league=self.league).values_list(
                "id", "points", "correct", "rank"
            )
        )
        ms_after = list(
            MemberSeason.objects.filter(league=self.league).values_list(
                "id", "points", "correct", "rank"
            )
        )
        picks_after = list(Pick.objects.filter(league=self.league).values_list("id", "is_correct"))
        self.assertEqual(mw_before, mw_after)
        self.assertEqual(ms_before, ms_after)
        self.assertEqual(picks_before, picks_after)

    def test_season_respects_drop_weeks(self):
        # User A: strong week1, strong week2 hypo → drop weaker if any
        # User B: weak everywhere
        result = simulate_standings(
            self.league,
            self.week2,
            self.rules,
            {self.w2_live.id: "home"},
            use_season_drops=True,
        )
        by_user = {row["user_id"]: row for row in result.season_standings}
        # With drop_weeks=1 and 2 weeks, one week is dropped.
        # A has 1 pt week1 + 2 pts week2 = 3 full; drops worst (1) → 2 adjusted
        self.assertEqual(by_user[self.user_a.id]["points"], 2)
        self.assertEqual(by_user[self.user_a.id]["hypo_rank"], 1)

    def test_endpoint_rejects_unstarted_and_returns_json(self):
        self.client.force_login(self.user_a)
        url = reverse("standings_what_if")
        resp = self.client.post(
            url,
            data={
                "league_id": self.league.id,
                "week_id": self.week2.id,
                "outcomes": {str(self.w2_upcoming.id): "home"},
            },
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])

        resp_ok = self.client.post(
            url,
            data={
                "league_id": self.league.id,
                "week_id": self.week2.id,
                "outcomes": {str(self.w2_live.id): "home"},
            },
            content_type="application/json",
        )
        self.assertEqual(resp_ok.status_code, 200)
        payload = resp_ok.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["week_standings"]), 2)
        self.assertEqual(len(payload["season_standings"]), 2)

    def test_standings_what_if_tab_renders(self):
        self.client.force_login(self.user_a)
        resp = self.client.get(
            reverse("standings"),
            {"league_id": self.league.id, "what_if": "true", "week": self.week2.id},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hypothetical")
        self.assertContains(resp, "what-if-panel")

