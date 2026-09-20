import logging
import smtplib

from django.conf import settings
from django.core.mail import BadHeaderError, EmailMessage
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_contact_emails(*, name, email, subject, message, user=None):
    """
    Send a support ticket from DEFAULT_FROM_EMAIL with Reply-To set to the
    sender, then email the sender a confirmation.
    """
    support_to = getattr(settings, "SUPPORT_EMAIL", "support@bigpicks.app")
    context = {
        "name": name,
        "email": email,
        "subject": subject,
        "message": message,
        "user": user,
        "support_email": support_to,
    }

    support_body = render_to_string("cfb/email/contact_support.txt", context)
    confirm_body = render_to_string("cfb/email/contact_confirmation.txt", context)

    support_msg = EmailMessage(
        subject=f"[BigPicks Contact] {subject}",
        body=support_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[support_to],
        reply_to=[email],
    )
    confirm_msg = EmailMessage(
        subject="We received your message — BigPicks",
        body=confirm_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )

    try:
        support_msg.send(fail_silently=False)
        confirm_msg.send(fail_silently=False)
    except (OSError, smtplib.SMTPException, BadHeaderError):
        logger.exception(
            "Failed to send contact email from %s (user_id=%s)",
            email,
            getattr(user, "pk", None),
        )
        return False
    return True
