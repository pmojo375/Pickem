from django.core.management.base import BaseCommand
from django.utils import timezone
import pytz
from cfb.models import Game


class Command(BaseCommand):
    help = 'Fix any naive datetimes by converting them to timezone-aware (assumes UTC)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without actually changing it',
        )

    def handle(self, *args, **options):
        games = Game.objects.all()
        total = games.count()
        fixed_count = 0
        dry_run = options['dry_run']
        
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes will be saved\n"))
        
        self.stdout.write(f"Checking {total} games...")
        
        for game in games:
            if not timezone.is_aware(game.start_time):
                fixed_count += 1
                old_time = game.start_time
                # Assume naive times are UTC
                new_time = timezone.make_aware(game.start_time, pytz.UTC)
                
                self.stdout.write(
                    f"Game {game.id}: {old_time} → {new_time}"
                )
                
                if not dry_run:
                    game.start_time = new_time
                    game.save(update_fields=['start_time'])
        
        if fixed_count > 0:
            if dry_run:
                self.stdout.write(
                    self.style.WARNING(f"\nWould fix {fixed_count} naive datetimes")
                )
                self.stdout.write("Run without --dry-run to apply changes")
            else:
                self.stdout.write(
                    self.style.SUCCESS(f"\n✓ Fixed {fixed_count} naive datetimes")
                )
        else:
            self.stdout.write(self.style.SUCCESS("\n✓ All times are already timezone-aware!"))

