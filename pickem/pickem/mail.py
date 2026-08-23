import logging

from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger("pickem.mail")


class LoggingEmailBackend(BaseEmailBackend):
    """Write mail to the app logger so it still shows while debugging.

    Django's console backend prints to stdout, which the debugger often
    swallows into Debug Console instead of the runserver terminal.
    """

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        sent = 0
        for message in email_messages:
            logger.info(
                "Email to %s | %s\n%s",
                ", ".join(message.to),
                message.subject,
                message.body,
            )
            sent += 1
        return sent
