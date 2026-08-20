from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings

from cfb.models import League, LeagueMembership, Season
from cfb.services import invites

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
