import logging
import sys
from datetime import datetime

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger("cfb")


class LoggingEmailBackend(BaseEmailBackend):
    """Dump local mail to the terminal and logs/email.log."""

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        sent = 0
        chunks = []
        for message in email_messages:
            block = (
                f"\n{'=' * 72}\n"
                f"EMAIL  {datetime.now().isoformat(timespec='seconds')}\n"
                f"To: {', '.join(message.to)}\n"
                f"Subject: {message.subject}\n"
                f"{'-' * 72}\n"
                f"{message.body}\n"
                f"{'=' * 72}\n"
            )
            chunks.append(block)
            logger.info("Email to %s | %s", ", ".join(message.to), message.subject)
            sent += 1
        dump = "".join(chunks)
        print(dump, file=sys.stderr, flush=True)
        log_path = settings.LOG_DIR / "email.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(dump)
        return sent
