from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings

from cfb.models import League, LeagueMembership, LeagueRules, Season
from cfb.services import invites
from cfb.services.payouts import build_payout_summary

User = get_user_model()


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

    def test_existing_user_gets_league_invite(self):
        User.objects.create_user("friend", "friend@example.com", "pass")
        result, email = invites.send_league_email_invite(
            self.request, self.league, "friend@example.com", season=self.season
        )
        self.assertEqual(result, "existing_sent")
        self.assertEqual(email, "friend@example.com")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("invited you to join", mail.outbox[0].body)
        self.assertIn("/leagues/invite/", mail.outbox[0].body)
        self.assertNotIn("/accounts/signup/", mail.outbox[0].body)

    def test_unknown_email_gets_join_site_and_league_invite(self):
        result, email = invites.send_league_email_invite(
            self.request, self.league, "newbie@example.com", season=self.season
        )
        self.assertEqual(result, "new_sent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/accounts/signup/", mail.outbox[0].body)
        self.assertIn("/leagues/invite/", mail.outbox[0].body)

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

