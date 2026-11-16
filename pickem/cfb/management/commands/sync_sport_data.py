from datetime import date
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from cfb.models import Season, Sport
from cfb.services.imports import sync_sport_data
from cfb.services.providers import ProviderError


class Command(BaseCommand):
    help = "Import sport data with an explicit date window and request budget. Basketball uses CFBD_API_KEY."

    def add_arguments(self, parser):
        parser.add_argument("--sport", required=True)
        parser.add_argument("--season", required=True, type=int)
        parser.add_argument("--start-date", type=date.fromisoformat)
        parser.add_argument("--end-date", type=date.fromisoformat)
        parser.add_argument("--season-type", choices=["regular", "postseason", "preseason"])
        parser.add_argument("--tournament", help="Provider tournament name, e.g. NCAA")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--teams-only", action="store_true")
        mode.add_argument("--games-only", action="store_true")
        parser.add_argument("--include-lines", action="store_true")
        parser.add_argument("--max-requests", type=int, default=3)
        parser.add_argument("--force", action="store_true", help="Bypass cached responses; still respects request budget and rate-limit cooldown")
        parser.add_argument("--dry-run", action="store_true", help="Print the plan without any network calls or database changes")

    def handle(self, *args, **options):
        try:
            sport = Sport.objects.get(pk=options["sport"], is_active=True)
            season = Season.objects.get(sport=sport, year=options["season"])
        except (Sport.DoesNotExist, Season.DoesNotExist):
            raise CommandError("Configure this sport and create its season before importing.")
        if sport.data_provider != "cbbd":
            raise CommandError("This command currently supports CBBD. Use initialize_season for football.")
        if not options["teams_only"] and (not options["start_date"] or not options["end_date"] or options["start_date"] > options["end_date"]):
            raise CommandError("Provide --start-date and --end-date in order to bound the import.")
        if not 1 <= options["max_requests"] <= 20:
            raise CommandError("--max-requests must be between 1 and 20.")
        if options["dry_run"]:
            calls = int(not options["games_only"]) + int(not options["teams_only"]) + int(options["include_lines"] and not options["teams_only"])
            self.stdout.write(f"Plan: {sport.name} {season.year}; at most {calls} requests, budget {options['max_requests']}. No requests sent.")
            return
        try:
            result = sync_sport_data(season, start=options["start_date"], end=options["end_date"], season_type=options["season_type"], tournament=options["tournament"], teams_only=options["teams_only"], games_only=options["games_only"], include_lines=options["include_lines"], max_requests=options["max_requests"], force=options["force"])
        except (ProviderError, ValidationError, KeyError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Stored {result['teams']} teams, {result['games']} games, {result['lines']} lines; {result['requests']} API requests."))
