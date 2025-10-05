"""
Cross-platform management command to start Celery beat scheduler.
Works on Windows, Linux, and macOS.
"""
import sys
import subprocess
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Start Celery beat scheduler (works on all platforms)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--loglevel',
            type=str,
            default='info',
            help='Log level (debug, info, warning, error, critical)',
        )

    def handle(self, *args, **options):
        loglevel = options['loglevel']

        # Detect operating system
        is_windows = sys.platform.startswith('win')
        
        self.stdout.write(self.style.HTTP_INFO('=== Celery Beat Scheduler Startup ==='))
        self.stdout.write(f'Operating System: {sys.platform}')
        self.stdout.write(f'Log Level: {loglevel}')

        # Build celery beat command
        cmd = ['celery', '-A', 'pickem', 'beat', f'--loglevel={loglevel}']

        if is_windows:
            self.stdout.write(
                self.style.WARNING(
                    '\n⚠️  Windows detected: Beat scheduler will work, but ensure worker is running!\n'
                )
            )

        # Display the command
        self.stdout.write(
            self.style.HTTP_INFO(f'\n📋 Command: {" ".join(cmd)}\n')
        )
        
        self.stdout.write(self.style.SUCCESS('Starting Celery beat scheduler...\n'))
        self.stdout.write('This will schedule periodic tasks according to CELERY_BEAT_SCHEDULE\n')
        self.stdout.write('Press Ctrl+C to stop\n')
        self.stdout.write('─' * 60)

        # Execute celery beat
        try:
            subprocess.run(cmd, check=True)
        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING('\n\n⚠️  Scheduler stopped by user (Ctrl+C)')
            )
        except subprocess.CalledProcessError as e:
            self.stdout.write(
                self.style.ERROR(f'\n\n✗ Scheduler failed with exit code {e.returncode}')
            )
            sys.exit(1)
        except FileNotFoundError:
            self.stdout.write(
                self.style.ERROR(
                    '\n✗ Celery not found! Install it with:\n'
                    '  pip install celery[redis]'
                )
            )
            sys.exit(1)

