"""
Management command to test Celery setup and verify it's working correctly.
Tests worker connection, task execution, and Redis connectivity.
"""
import time
from django.core.management.base import BaseCommand
from django.core.cache import cache
from celery import current_app
from cfb.tasks import poll_espn_scores


class Command(BaseCommand):
    help = 'Test Celery worker and Redis connectivity'

    def add_arguments(self, parser):
        parser.add_argument(
            '--timeout',
            type=int,
            default=30,
            help='Maximum seconds to wait for task completion',
        )

    def handle(self, *args, **options):
        timeout = options['timeout']
        
        self.stdout.write(self.style.HTTP_INFO('=== Celery System Test ===\n'))

        # Test 1: Check Celery configuration
        self.stdout.write('1️⃣  Testing Celery configuration...')
        try:
            broker_url = current_app.conf.broker_url
            result_backend = current_app.conf.result_backend
            
            self.stdout.write(self.style.SUCCESS('   ✓ Celery configured'))
            self.stdout.write(f'   Broker: {broker_url}')
            self.stdout.write(f'   Backend: {result_backend}\n')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   ✗ Configuration error: {e}\n'))
            return

        # Test 2: Check Redis connectivity
        self.stdout.write('2️⃣  Testing Redis connectivity...')
        try:
            test_key = 'celery_test_key'
            test_value = 'test_value'
            cache.set(test_key, test_value, timeout=10)
            retrieved = cache.get(test_key)
            
            if retrieved == test_value:
                self.stdout.write(self.style.SUCCESS('   ✓ Redis is accessible'))
                cache.delete(test_key)
            else:
                self.stdout.write(self.style.ERROR('   ✗ Redis test failed (value mismatch)'))
                return
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   ✗ Redis error: {e}'))
            self.stdout.write('   Make sure Redis is running: redis-cli ping\n')
            return

        # Test 3: Check for active workers
        self.stdout.write('\n3️⃣  Checking for active Celery workers...')
        try:
            inspector = current_app.control.inspect()
            active_workers = inspector.active()
            
            if active_workers:
                self.stdout.write(self.style.SUCCESS(f'   ✓ Found {len(active_workers)} active worker(s)'))
                for worker_name in active_workers.keys():
                    self.stdout.write(f'   - {worker_name}')
            else:
                self.stdout.write(
                    self.style.ERROR(
                        '   ✗ No active workers found!\n'
                        '   Start a worker with:\n'
                        '     python manage.py start_celery_worker\n'
                    )
                )
                return
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   ✗ Could not inspect workers: {e}'))
            return

        # Test 4: Queue a test task (debug_task)
        self.stdout.write('\n4️⃣  Queueing a test task...')
        try:
            from pickem.celery import debug_task
            result = debug_task.delay()
            
            self.stdout.write(f'   Task ID: {result.id}')
            self.stdout.write(f'   Waiting up to {timeout} seconds for completion...')
            
            # Wait for task to complete
            start_time = time.time()
            while not result.ready() and (time.time() - start_time) < timeout:
                time.sleep(1)
                elapsed = int(time.time() - start_time)
                self.stdout.write(f'   ⏳ {elapsed}s...', ending='\r')
                self.stdout.flush()
            
            if result.ready():
                if result.successful():
                    self.stdout.write(self.style.SUCCESS('\n   ✓ Test task completed successfully!'))
                else:
                    self.stdout.write(self.style.ERROR(f'\n   ✗ Test task failed: {result.result}'))
                    return
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f'\n   ⚠️  Task did not complete within {timeout}s'
                        '\n   Worker may be busy or not running'
                    )
                )
                return
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n   ✗ Error queueing task: {e}'))
            return

        # Test 5: Test ESPN polling task (async, don't wait)
        self.stdout.write('\n5️⃣  Testing ESPN polling task...')
        try:
            result = poll_espn_scores.delay()
            self.stdout.write(f'   Task ID: {result.id}')
            self.stdout.write('   Task queued (running in background)')
            self.stdout.write('   Check worker logs for results')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   ✗ Error queueing ESPN task: {e}'))

        # Test 6: Check scheduled tasks (Beat)
        self.stdout.write('\n6️⃣  Checking Celery Beat schedule...')
        try:
            scheduled = current_app.conf.beat_schedule
            if scheduled:
                self.stdout.write(self.style.SUCCESS(f'   ✓ Found {len(scheduled)} scheduled task(s)'))
                for task_name, task_config in scheduled.items():
                    schedule = task_config.get('schedule', 'unknown')
                    self.stdout.write(f'   - {task_name}: every {schedule}s')
                
                self.stdout.write(
                    '\n   ℹ️  To run scheduled tasks, start Celery Beat:\n'
                    '     python manage.py start_celery_beat'
                )
            else:
                self.stdout.write(self.style.WARNING('   ⚠️  No scheduled tasks found'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   ✗ Error checking schedule: {e}'))

        # Summary
        self.stdout.write(self.style.HTTP_INFO('\n' + '═' * 60))
        self.stdout.write(self.style.SUCCESS('✅ Celery system is working correctly!\n'))
        
        self.stdout.write('Next steps:')
        self.stdout.write('  1. Keep worker running: python manage.py start_celery_worker')
        self.stdout.write('  2. Start scheduler: python manage.py start_celery_beat')
        self.stdout.write('  3. Monitor system: python manage.py check_system_status')
        self.stdout.write('  4. Test polling: python manage.py poll_espn_now\n')

