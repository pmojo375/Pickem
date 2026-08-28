from decimal import Decimal

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from cfb.models import League, LeagueInvite, LeagueMembership, LeagueRules, Season
from cfb.services import invites
from cfb.services.payouts import build_payout_summary

User = get_user_model()


def _verify_email(user):
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"verified": True, "primary": True},
    )


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

