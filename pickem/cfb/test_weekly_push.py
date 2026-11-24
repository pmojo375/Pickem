import base64
import json
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import http_ece
from requests import Response
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pywebpush import WebPushException

from .models import Game, League, LeagueGame, LeagueMembership, PushSubscription, Season, Team, Week, WeeklyPickPush
from .services.weekly_push import deliver_pending_pushes


def encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@override_settings(WEB_PUSH_PUBLIC_KEY="test-key", WEB_PUSH_PRIVATE_KEY="test-private-key")
class WeeklyPushTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user("manager")
        cls.member = User.objects.create_user("member")
        cls.league = League.objects.create(name="Spartans", created_by=cls.owner)
        LeagueMembership.objects.create(league=cls.league, user=cls.owner, role="owner")
        LeagueMembership.objects.create(league=cls.league, user=cls.member)
        season = Season.objects.create(year=2026, is_active=True)
        today = timezone.localdate()
        cls.week = Week.objects.create(season=season, number=2, start_date=today, end_date=today + timedelta(days=6))
        cls.game = Game.objects.create(
            season=season, home_team=Team.objects.create(season=season, name="Home"),
            away_team=Team.objects.create(season=season, name="Away"),
            # A moved game can be in the display window without a provider week.
            kickoff=timezone.now() + timedelta(days=2),
        )
        point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
        )
        cls.keys = {"p256dh": encode(point), "auth": encode(bytes(range(16)))}
        cls.endpoint = "https://fcm.googleapis.com/fcm/send/device-a"

    def api(self, action, **overrides):
        return self.client.post(reverse("push_subscription"), {
            "action": action, "endpoint": self.endpoint, "keys": self.keys, **overrides,
        }, content_type="application/json")

    def device(self, user, endpoint):
        return PushSubscription.objects.create(user=user, endpoint=endpoint, **self.keys)

    def selections(self, selected=True, **extra):
        data = {"do": "save_selections", "league_id": self.league.pk, "week_id": self.week.pk,
                f"game_{self.game.pk}_id": self.game.pk, **extra}
        if selected:
            data[f"game_{self.game.pk}_select"] = "on"
        return self.client.post(reverse("settings"), data)

    def test_subscription_requires_login_csrf_valid_keys_and_push_service(self):
        self.assertEqual(self.api("enable").status_code, 302)
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.member)
        self.assertEqual(self.api("enable").status_code, 403)
        self.client = Client()
        self.client.force_login(self.member)
        for endpoint in ("https://example.org/send", "http://fcm.googleapis.com/send",
                         "https://fcm.googleapis.com.example.org/send", "https://fcm.googleapis.com@localhost/send"):
            self.assertEqual(self.api("enable", endpoint=endpoint).status_code, 400)
        self.assertEqual(self.api("enable", keys={"p256dh": "invalid"}).status_code, 400)
        self.assertFalse(PushSubscription.objects.exists())

    def test_device_ownership_idempotency_and_logout(self):
        self.client.force_login(self.member)
        self.assertTrue(self.api("enable").json()["enabled"])
        self.assertTrue(self.api("enable").json()["enabled"])
        self.assertEqual(PushSubscription.objects.count(), 1)
        # A second account in a different session can't transfer or disable this device.
        first_client = self.client
        self.client = Client()
        self.client.force_login(self.owner)
        self.assertFalse(self.api("status").json()["enabled"])
        self.assertEqual(self.api("enable").status_code, 409)
        self.api("disable")
        self.assertEqual(PushSubscription.objects.get().user, self.member)
        first_client.logout()
        self.assertFalse(PushSubscription.objects.exists())

    def test_only_first_slate_reaches_active_members_devices(self):
        other = get_user_model().objects.create_user("other")
        inactive = get_user_model().objects.create_user("inactive")
        LeagueMembership.objects.create(league=self.league, user=inactive, is_active=False)
        for user in (other, inactive, self.member):
            self.device(user, f"{self.endpoint}-{user.pk}")
        self.device(self.member, self.endpoint)
        self.client.force_login(self.member)
        self.selections()
        self.assertFalse(LeagueGame.objects.exists())
        self.client.force_login(self.owner)
        self.selections(False)
        self.selections(week_id=99999)
        self.assertFalse(WeeklyPickPush.objects.exists())
        self.selections()
        self.assertEqual(list(WeeklyPickPush.objects.values_list("subscription__user_id", flat=True)),
                         [self.member.pk, self.member.pk])
        WeeklyPickPush.objects.all().delete()  # Simulate completed deliveries.
        self.selections()
        self.selections(False)
        self.selections(lock_spread="on")
        self.assertTrue(LeagueGame.objects.get().is_active)
        self.assertFalse(WeeklyPickPush.objects.exists())

    @override_settings(POSTHOG_MW_CAPTURE_EXCEPTIONS=False)
    def test_publication_rolls_back_with_selection(self):
        self.client.force_login(self.owner)
        with patch("cfb.views.queue_weekly_pushes", side_effect=RuntimeError("rollback")):
            with self.assertRaises(RuntimeError):
                self.selections()
        self.assertFalse(LeagueGame.objects.exists())

    @patch("cfb.services.weekly_push.webpush")
    def test_delivery_retry_deep_link_and_expired_device(self, send):
        self.device(self.member, self.endpoint)
        self.client.force_login(self.owner)
        self.selections()
        send.side_effect = WebPushException("busy", response=Mock(status_code=503))
        deliver_pending_pushes()
        pending = WeeklyPickPush.objects.get()
        self.assertGreater(pending.next_attempt_at, timezone.now())
        deliver_pending_pushes()
        self.assertEqual(send.call_count, 1)
        WeeklyPickPush.objects.update(next_attempt_at=timezone.now())
        send.side_effect = None
        deliver_pending_pushes()
        payload = json.loads(send.call_args.kwargs["data"])
        self.assertIn("Week 2", payload["title"])
        self.assertIn("Spartans", payload["body"])
        self.assertEqual(payload["url"], f"/picks/?league_id={self.league.pk}")
        self.assertFalse(WeeklyPickPush.objects.exists())
        WeeklyPickPush.objects.create(league=self.league, week=self.week, subscription=PushSubscription.objects.get())
        send.side_effect = WebPushException("expired", response=Mock(status_code=410))
        deliver_pending_pushes()
        self.assertFalse(PushSubscription.objects.exists())

    @patch("requests.Session.send")
    def test_provider_receives_a_signed_encrypted_notification(self, send):
        device_key = ec.generate_private_key(ec.SECP256R1())
        point = device_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
        )
        self.client.force_login(self.member)
        self.assertTrue(self.api("enable", keys={**self.keys, "p256dh": encode(point)}).json()["enabled"])
        self.client.force_login(self.owner)
        self.selections()
        signing_key = ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value
        accepted = Response()
        accepted.status_code = 201
        send.return_value = accepted
        with self.settings(WEB_PUSH_PRIVATE_KEY=encode(signing_key.to_bytes(32, "big"))):
            deliver_pending_pushes()
        request = send.call_args.args[0]
        self.assertEqual(request.url, self.endpoint)
        self.assertTrue(request.headers["Authorization"].startswith("vapid "))
        self.assertEqual(request.headers["Content-Encoding"], "aes128gcm")
        payload = json.loads(http_ece.decrypt(request.body, private_key=device_key, auth_secret=bytes(range(16))))
        self.assertEqual(payload["tag"], f"picks-{self.league.pk}-{self.week.pk}")
        self.assertFalse(WeeklyPickPush.objects.exists())

    @patch("cfb.services.weekly_push.webpush")
    def test_leaving_league_before_delivery(self, send):
        self.device(self.member, self.endpoint)
        self.client.force_login(self.owner)
        self.selections()
        LeagueMembership.objects.filter(user=self.member).update(is_active=False)
        deliver_pending_pushes()
        send.assert_not_called()
        self.assertFalse(WeeklyPickPush.objects.exists())

    @override_settings(DEBUG=False)
    def test_worker_and_account_render_before_collectstatic(self):
        self.client.force_login(self.member)
        with TemporaryDirectory() as empty_root, self.settings(STATIC_ROOT=empty_root):
            self.assertContains(self.client.get(reverse("account")), "/static/pwa/manifest.webmanifest")
            self.assertContains(self.client.get(reverse("service_worker")), "/static/pwa/icon-192.png")

    @override_settings(WEB_PUSH_PRIVATE_KEY="")
    def test_unconfigured_push_leaves_install_and_picks_available(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.api("enable").status_code, 503)
        self.selections()
        self.assertTrue(LeagueGame.objects.get().is_active)
        self.assertFalse(WeeklyPickPush.objects.exists())
        self.assertContains(self.client.get(reverse("account")), 'rel="manifest"')
        self.client.logout()
        worker = self.client.get(reverse("service_worker"))
        self.assertEqual(worker.status_code, 200)
        self.assertEqual(worker["Content-Type"], "application/javascript")
        self.assertIn("no-cache", worker["Cache-Control"])
