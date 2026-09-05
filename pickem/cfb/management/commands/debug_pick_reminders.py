"""Inspect and optionally force-send incomplete-pick reminder emails."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from cfb.models import League, LeagueRules
from cfb.services.reminders import process_league_reminders
from cfb.services.schedule import get_current_week, get_display_week


class Command(BaseCommand):
    help = (
        "Show who would get pick reminder emails, or force-send them. "
        "Use --dry-run first; --force bypasses the pre-kickoff time window."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--league-id",
            type=int,
            help="Only process this league (recommended for testing).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List incomplete members without sending email.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Ignore the hours-before-kickoff window.",
        )
        parser.add_argument(
            "--clear-sent",
            action="store_true",
            help="Clear the Redis already-sent flag before running.",
        )
        parser.add_argument(
            "--include-disabled",
            action="store_true",
            help="Include leagues that have not enabled the reminder toggle.",
        )

    def handle(self, *args, **options):
        week = get_current_week() or get_display_week()
        if not week:
            raise CommandError("No current/display week found.")

        dry_run = options["dry_run"]
        force = options["force"]
        clear_sent = options["clear_sent"]

        if not dry_run and not force:
            self.stdout.write(
                self.style.WARNING(
                    "Running real send in the normal time window. "
                    "Prefer --dry-run, or --force --dry-run for a preview."
                )
            )

        rules_qs = LeagueRules.objects.filter(season=week.season).select_related(
            "league"
        )
        if options["league_id"]:
            rules_qs = rules_qs.filter(league_id=options["league_id"])
            if not rules_qs.exists():
                raise CommandError(
                    f"No league rules for league_id={options['league_id']} "
                    f"in season {week.season.year}."
                )
        if not options["include_disabled"]:
            rules_qs = rules_qs.filter(pick_reminder_emails_enabled=True)

        if not rules_qs.exists():
            raise CommandError(
                "No matching leagues. Enable the toggle, pass --league-id, "
                "or use --include-disabled."
            )

        self.stdout.write(
            f"Week {week.number} ({week.season_type}) {week.season.year} | "
            f"hours_before={settings.PICK_REMINDER_HOURS_BEFORE_KICKOFF} | "
            f"now={timezone.localtime()} | "
            f"dry_run={dry_run} force={force}"
        )

        for rules in rules_qs:
            league = rules.league
            result = process_league_reminders(
                rules,
                week,
                force=force,
                dry_run=dry_run,
                clear_sent=clear_sent,
            )
            kickoff = result.get("first_kickoff")
            reminder_at = result.get("reminder_at")
            self.stdout.write("")
            self.stdout.write(
                self.style.HTTP_INFO(
                    f"League {league.id} {league.name} "
                    f"(reminders_enabled={rules.pick_reminder_emails_enabled})"
                )
            )
            self.stdout.write(f"  status: {result['status']}")
            if kickoff:
                self.stdout.write(f"  first_kickoff: {timezone.localtime(kickoff)}")
            if reminder_at:
                self.stdout.write(f"  reminder_at:   {timezone.localtime(reminder_at)}")
            self.stdout.write(
                f"  sent={result['sent']} would_send={result['would_send']} "
                f"skipped={result['skipped']} failed={result['failed']}"
            )
            for recipient in result["recipients"]:
                self.stdout.write(
                    f"  - {recipient['username']} <{recipient['email']}> "
                    f"picks {recipient['picks_made']}/{recipient['picks_required']} "
                    f"keys {recipient['key_picks_made']}/{recipient['key_picks_required']}"
                )

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    "\nDry run only — no emails sent. "
                    "To actually send: drop --dry-run and add --force "
                    "(and --clear-sent if you already tested)."
                )
            )
