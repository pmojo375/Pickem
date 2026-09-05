"""Print active league member emails for group BCC / mailing."""
from django.core.management.base import BaseCommand, CommandError

from cfb.models import League, LeagueMembership, user_notification_emails


class Command(BaseCommand):
    help = (
        "List emails for active members of a league "
        "(comma-separated by default, ready to paste into BCC)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--league",
            type=str,
            default="Spartans",
            help='League name (default: "Spartans").',
        )
        parser.add_argument(
            "--one-per-line",
            action="store_true",
            help="Print one email per line instead of a comma-separated list.",
        )
        parser.add_argument(
            "--include-names",
            action="store_true",
            help="Include username alongside each email.",
        )

    def handle(self, *args, **options):
        league_name = options["league"]
        league = League.objects.filter(name__iexact=league_name).first()
        if not league:
            raise CommandError(f'League "{league_name}" not found.')

        memberships = (
            LeagueMembership.objects.filter(league=league, is_active=True)
            .select_related("user", "user__profile")
            .order_by("user__username")
        )

        rows = []
        missing = []
        member_count = 0
        for membership in memberships:
            member_count += 1
            user = membership.user
            emails = user_notification_emails(user)
            if not emails:
                missing.append(user.username)
                continue
            for email in emails:
                if options["include_names"]:
                    rows.append(f"{user.username} <{email}>")
                else:
                    rows.append(email)

        self.stdout.write(
            self.style.SUCCESS(
                f"{league.name}: {len(rows)} email address(es) "
                f"across {member_count} active member(s)"
                + (f", {len(missing)} missing email" if missing else "")
            )
        )

        if not rows:
            self.stdout.write(self.style.WARNING("No emails to print."))
        elif options["one_per_line"] or options["include_names"]:
            for row in rows:
                self.stdout.write(row)
        else:
            self.stdout.write(", ".join(rows))

        if missing:
            self.stdout.write(
                self.style.WARNING(
                    "Active members with no email: " + ", ".join(missing)
                )
            )
