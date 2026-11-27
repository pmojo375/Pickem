import base64
import json
import re
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .models import PushSubscription

# Subscriptions come from browsers, but the worker makes the outbound request.
PUSH_HOST = re.compile(
    r"fcm\.googleapis\.com|(?:[a-z0-9-]+\.)*push\.services\.mozilla\.com|"
    r"(?:[a-z0-9-]+\.)*push\.apple\.com|(?:[a-z0-9-]+\.)+notify\.windows\.com"
)
SESSION_DEVICE = "weekly_push_device"


def public_key():
    if settings.WEB_PUSH_PRIVATE_KEY:
        return settings.WEB_PUSH_PUBLIC_KEY
    return ""


def valid_endpoint(endpoint):
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        return False
    if not endpoint.isascii() or any(ord(char) < 33 for char in endpoint) or "\\" in endpoint:
        return False
    url = urlsplit(endpoint)
    return (
        url.scheme == "https"
        and PUSH_HOST.fullmatch(url.netloc) is not None
        and url.path.startswith("/")
        and not url.fragment
    )


def decode_key(value, size):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if len(raw) != size:
        raise ValueError
    return raw


@never_cache
@require_GET
def service_worker(request):
    return HttpResponse(render_to_string("cfb/service-worker.js"), content_type="application/javascript")


@login_required
@require_POST
def push_subscription(request):
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict) or not valid_endpoint(data.get("endpoint")):
            raise ValueError
        endpoint = data["endpoint"]
        action = data.get("action")
        devices = PushSubscription.objects.filter(user=request.user, endpoint=endpoint)
        if action == "disable":
            devices.delete()
            return JsonResponse({"enabled": False})
        if action in ("status", "preferences"):
            if action == "preferences":
                prefs = {key: data.get(key) for key in ("remind_day", "remind_hours")}
                if any(type(value) is not bool for value in prefs.values()):
                    raise ValueError
                devices.update(**prefs)
            device = devices.first()
            if device:
                request.session[SESSION_DEVICE] = device.pk
            return JsonResponse({"enabled": bool(device), "remind_day": bool(device and device.remind_day),
                                 "remind_hours": bool(device and device.remind_hours)})
        if action != "enable":
            raise ValueError
        keys = data.get("keys")
        if not isinstance(keys, dict):
            raise ValueError
        point = decode_key(keys.get("p256dh"), 65)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
        decode_key(keys.get("auth"), 16)
    except ValueError:
        return JsonResponse({"error": "Invalid push subscription."}, status=400)

    if not public_key():
        return JsonResponse({"error": "Weekly alerts are unavailable."}, status=503)
    device, _ = PushSubscription.objects.get_or_create(
        endpoint=endpoint, defaults={"user": request.user, "p256dh": keys["p256dh"], "auth": keys["auth"]}
    )
    if (device.user_id, device.p256dh, device.auth) != (request.user.pk, keys["p256dh"], keys["auth"]):
        return JsonResponse({"error": "Please renew this browser subscription."}, status=409)
    request.session[SESSION_DEVICE] = device.pk
    return JsonResponse({"enabled": True})
