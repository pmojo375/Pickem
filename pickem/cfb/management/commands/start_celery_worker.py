"""
Cross-platform management command to start Celery worker.
Automatically detects OS and uses appropriate pool implementation.

Windows: Uses --pool=solo (required, as fork is not supported)
Linux/Mac: Uses --pool=prefork with concurrency (better performance)
"""
import sys
import os
import subprocess
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Start Celery worker with OS-appropriate settings'

    def add_arguments(self, parser):
        parser.add_argument(
            '--loglevel',
            type=str,
            default='info',
            help='Log level (debug, info, warning, error, critical)',
        )
        parser.add_argument(
            '--concurrency',
            type=int,
            default=4,
            help='Number of worker processes (Linux/Mac only)',
        )
        parser.add_argument(
            '--queue',
            type=str,
            default='celery,scores',
            help='Comma-separated list of queues to consume',
        )

    def handle(self, *args, **options):
        loglevel = options['loglevel']
        concurrency = options['concurrency']
        queue = options['queue']

        # Detect operating system
        is_windows = sys.platform.startswith('win')
        is_linux = sys.platform.startswith('linux')
        is_mac = sys.platform == 'darwin'

        self.stdout.write(self.style.HTTP_INFO('=== Celery Worker Startup ==='))
        self.stdout.write(f'Operating System: {sys.platform}')
        self.stdout.write(f'Python Version: {sys.version.split()[0]}')
        self.stdout.write(f'Log Level: {loglevel}')
        self.stdout.write(f'Queues: {queue}')

        # Build celery command
        cmd = ['celery', '-A', 'pickem', 'worker', f'--loglevel={loglevel}']

        # Add queue specification
        cmd.extend(['-Q', queue])

        if is_windows:
            # Windows-specific settings
            self.stdout.write(
                self.style.WARNING(
                    '\n⚠️  Windows detected: Using --pool=solo (fork not supported on Windows)'
                )
            )
            cmd.append('--pool=solo')
            
            # Additional Windows recommendations
            self.stdout.write(
                '\nℹ️  Windows Tips:\n'
                '  - Solo pool means single-threaded execution\n'
                '  - For better performance, consider using WSL2 (Windows Subsystem for Linux)\n'
                '  - Or install gevent: pip install gevent, then use --pool=gevent'
            )

        elif is_linux or is_mac:
            # Linux/Mac - use prefork pool with multiple workers
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n✓ {("Linux" if is_linux else "macOS")} detected: '
                    f'Using --pool=prefork with {concurrency} workers'
                )
            )
            cmd.extend([
                '--pool=prefork',
                f'--concurrency={concurrency}'
            ])

        else:
            # Unknown OS - play it safe
            self.stdout.write(
                self.style.WARNING(
                    f'\n⚠️  Unknown OS ({sys.platform}): Using --pool=solo for safety'
                )
            )
            cmd.append('--pool=solo')

        # Display the command
        self.stdout.write(
            self.style.HTTP_INFO(f'\n📋 Command: {" ".join(cmd)}\n')
        )
        
        # Additional startup tips
        self.stdout.write(self.style.SUCCESS('Starting Celery worker...\n'))
        self.stdout.write('Press Ctrl+C to stop the worker\n')
        self.stdout.write('─' * 60)

        # Execute celery worker
        try:
            # Use subprocess to run celery
            # This allows proper signal handling and output streaming
            subprocess.run(cmd, check=True)
        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING('\n\n⚠️  Worker stopped by user (Ctrl+C)')
            )
        except subprocess.CalledProcessError as e:
            self.stdout.write(
                self.style.ERROR(f'\n\n✗ Worker failed with exit code {e.returncode}')
            )
            self.stdout.write(
                '\nTroubleshooting:\n'
                '  1. Ensure Redis is running: redis-cli ping\n'
                '  2. Check settings.py for CELERY_BROKER_URL\n'
                '  3. Verify you\'re in the correct directory (pickem/)\n'
                '  4. Try: pip install -r ../requirements.txt'
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

