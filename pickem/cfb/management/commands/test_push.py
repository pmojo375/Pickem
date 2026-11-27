import json
from uuid import uuid4

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from py_vapid import VapidException
from pywebpush import WebPushException, webpush

from cfb.models import PushSubscription
from cfb.pwa import valid_endpoint


class Command(BaseCommand):
    help = "Send a test Web Push notification to one active user's subscribed devices."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Exact account username")
        parser.add_argument("--device", type=int, help="Only send to this subscription ID")
        parser.add_argument("--list-devices", action="store_true", help="List subscription IDs without sending")

    def handle(self, *args, **options):
        user_model = get_user_model()
        try:
            user = user_model.objects.get(**{user_model.USERNAME_FIELD: options["username"], "is_active": True})
        except user_model.DoesNotExist:
            raise CommandError("Active account not found.") from None

        devices = PushSubscription.objects.filter(user=user).order_by("pk")
        if options["device"] is not None:
            devices = devices.filter(pk=options["device"])
        devices = list(devices)
        if not devices:
            raise CommandError("No matching subscriptions. Enable alerts in BigPicks Account first.")
        if options["list_devices"]:
            for device in devices:
                self.stdout.write(f"Subscription ID: {device.pk}")
            return

        if not all(getattr(settings, name, None) for name in (
            "WEB_PUSH_PRIVATE_KEY", "WEB_PUSH_PUBLIC_KEY", "WEB_PUSH_SUBJECT"
        )):
            raise CommandError("Web Push credentials are missing from the command environment.")

        payload = json.dumps({
            "title": "BigPicks test notification",
            "body": "Your push notifications are working! Tap to open BigPicks.",
            "url": "/picks/",
            "tag": f"test-{uuid4().hex}",
        })
        sent = 0
        with requests.Session() as session:
            session.max_redirects = 0
            for device in devices:
                try:
                    if not valid_endpoint(device.endpoint):
                        self.stderr.write(f"Subscription {device.pk}: invalid endpoint.")
                        continue
                    response = webpush(
                        subscription_info={
                            "endpoint": device.endpoint,
                            "keys": {"p256dh": device.p256dh, "auth": device.auth},
                        },
                        data=payload,
                        vapid_private_key=settings.WEB_PUSH_PRIVATE_KEY,
                        vapid_claims={"sub": settings.WEB_PUSH_SUBJECT},
                        requests_session=session,
                        ttl=300,
                        timeout=5,
                    )
                    if not 200 <= response.status_code < 300:
                        self.stderr.write(f"Subscription {device.pk}: HTTP {response.status_code}.")
                        continue
                except (WebPushException, requests.RequestException) as exc:
                    response = getattr(exc, "response", None)
                    status = response.status_code if response is not None else "unavailable"
                    self.stderr.write(f"Subscription {device.pk}: delivery failed (HTTP {status}).")
                    continue
                except (VapidException, OSError, ValueError):
                    raise CommandError("Unable to prepare push. Check signing key and subscription configuration.") from None
                sent += 1
                self.stdout.write(self.style.SUCCESS(f"Subscription {device.pk}: accepted by push provider."))

        self.stdout.write(f"Accepted: {sent}/{len(devices)}")
        if sent != len(devices):
            raise CommandError("One or more test notifications failed. No subscriptions were deleted.")
