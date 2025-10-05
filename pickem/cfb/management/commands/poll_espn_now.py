"""
Management command to manually trigger ESPN score polling.
Useful for testing and debugging the polling system.
"""
from django.core.management.base import BaseCommand
from cfb.tasks import poll_espn_scores


class Command(BaseCommand):
    help = 'Manually trigger ESPN score polling'

    def add_arguments(self, parser):
        parser.add_argument(
            '--async',
            action='store_true',
            help='Run the task asynchronously via Celery',
        )

    def handle(self, *args, **options):
        run_async = options['async']

        self.stdout.write(self.style.WARNING('Triggering ESPN score poll...'))

        if run_async:
            # Queue the task in Celery
            result = poll_espn_scores.delay()
            self.stdout.write(
                self.style.SUCCESS(f'Task queued with ID: {result.id}')
            )
        else:
            # Run synchronously
            poll_espn_scores()
            self.stdout.write(
                self.style.SUCCESS('ESPN score poll completed')
            )

