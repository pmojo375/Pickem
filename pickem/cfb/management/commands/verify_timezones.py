from django.core.management.base import BaseCommand
from django.utils import timezone
from cfb.models import Game


class Command(BaseCommand):
    help = 'Verify that all game kickoff times are timezone-aware'

    def handle(self, *args, **options):
        games = Game.objects.all()
        total = games.count()
        aware_count = 0
        naive_count = 0
        
        self.stdout.write(f"Checking {total} games...")
        
        for game in games:
            if timezone.is_aware(game.kickoff):
                aware_count += 1
            else:
                naive_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"Game {game.id} has naive datetime: {game.kickoff}"
                    )
                )
        
        self.stdout.write(self.style.SUCCESS(f"\n✓ Timezone-aware: {aware_count}"))
        if naive_count > 0:
            self.stdout.write(
                self.style.ERROR(f"✗ Naive (needs fixing): {naive_count}")
            )
        else:
            self.stdout.write(self.style.SUCCESS(f"✓ All times are timezone-aware!"))
        
        if naive_count > 0:
            self.stdout.write(
                "\nTo fix naive datetimes, you can run: python manage.py fix_naive_times"
            )

