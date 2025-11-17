"""
Celery configuration for pickem project.
"""
import os
from celery import Celery
from celery.schedules import crontab

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pickem.settings')

app = Celery('pickem')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Configure Celery to use Django's logging
from celery.signals import worker_process_init

@worker_process_init.connect
def config_worker_logging(**kwargs):
    """Configure logging for Celery worker processes."""
    import logging.config
    from django.conf import settings
    
    # Use Django's logging configuration for Celery workers
    if hasattr(settings, 'LOGGING'):
        logging.config.dictConfig(settings.LOGGING)


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Debug task for testing Celery setup."""
    print(f'Request: {self.request!r}')


# Dynamic Celery Beat schedule
# The actual interval is determined by the live state in Redis
app.conf.beat_schedule = {
    'poll-espn-scores': {
        'task': 'cfb.tasks.poll_espn_scores',
        'schedule': 60.0,  # Run every minute by default, task will self-regulate
        'options': {'expires': 55},  # Expire if not executed within 55 seconds
    },
    'adjust-polling-interval': {
        'task': 'cfb.tasks.adjust_polling_interval',
        'schedule': 30.0,  # Check every 30 seconds whether to adjust polling
        'options': {'expires': 25},
    },
    'cleanup-old-cache': {
        'task': 'cfb.tasks.cleanup_old_game_cache',
        'schedule': crontab(minute='*/15'),  # Every 15 minutes
        'options': {'expires': 600},
    },
    # Sync all season games once weekly on Monday at 6 AM
    'sync-season-games': {
        'task': 'cfb.tasks.pull_season_games',
        'schedule': crontab(day_of_week=1, hour=6, minute=0),  # Monday at 6 AM
        'options': {'expires': 3600},
        'kwargs': {'force': True},  # Force update to refresh all games
    },
    'sync-rankings': {
        'task': 'cfb.tasks.update_rankings',
        'schedule': crontab(day_of_week=1, hour=6, minute=0), # Monday at 12 AM
        'options': {'expires': 3600},
    },
    # Update team stats every Monday at 6 AM
    'update-team-stats': {
        'task': 'cfb.tasks.update_team_stats',
        'schedule': crontab(day_of_week=1, hour=6, minute=0),  # Monday at 6 AM
        'options': {'expires': 3600},
    },
    # Update spreads once daily at 9 AM
    'daily-spread-update': {
        'task': 'cfb.tasks.update_spreads',
        'schedule': crontab(hour=9, minute=0),  # Daily at 9 AM
        'options': {'expires': 3600},
    },
}

app.conf.task_routes = {
    'cfb.tasks.poll_espn_scores': {'queue': 'scores'},
    'cfb.tasks.adjust_polling_interval': {'queue': 'scores'},
    'cfb.tasks.update_single_game': {'queue': 'scores'},
    'cfb.tasks.pull_season_games': {'queue': 'scores'},
    'cfb.tasks.update_spreads': {'queue': 'scores'},
    'cfb.tasks.update_team_stats': {'queue': 'scores'},
}

# Worker configuration
app.conf.worker_prefetch_multiplier = 1
app.conf.worker_max_tasks_per_child = 1000
app.conf.task_acks_late = True
app.conf.task_reject_on_worker_lost = True

