"""
Management command to manually update a specific game.
Useful for testing and debugging.
"""
from django.core.management.base import BaseCommand, CommandError
from cfb.models import Game
from cfb.tasks import update_single_game


class Command(BaseCommand):
    help = 'Manually update a specific game from ESPN'

    def add_arguments(self, parser):
        parser.add_argument(
            'game_id',
            type=int,
            help='Database ID of the game to update',
        )
        parser.add_argument(
            '--async',
            action='store_true',
            help='Run the task asynchronously via Celery',
        )

    def handle(self, *args, **options):
        game_id = options['game_id']

        # Verify game exists
        try:
            game = Game.objects.select_related('home_team', 'away_team').get(id=game_id)
        except Game.DoesNotExist:
            raise CommandError(f'Game {game_id} does not exist')

        self.stdout.write(
            self.style.WARNING(
                f'Updating game: {game.away_team.name} @ {game.home_team.name}'
            )
        )

        run_async = options['async']

        if run_async:
            # Queue the task in Celery
            result = update_single_game.delay(game_id)
            self.stdout.write(
                self.style.SUCCESS(f'Task queued with ID: {result.id}')
            )
        else:
            # Run synchronously
            update_single_game(game_id)
            
            # Reload game to show updated data
            game.refresh_from_db()
            self.stdout.write(
                self.style.SUCCESS(
                    f'Game updated: {game.away_score or 0} - {game.home_score or 0} '
                    f'{"FINAL" if game.is_final else f"Q{game.quarter or 0}"}'
                )
            )

