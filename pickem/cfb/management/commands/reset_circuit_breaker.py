"""
Management command to reset the circuit breaker.
Useful when ESPN is back online after an outage.
"""
from django.core.management.base import BaseCommand
from django.core.cache import cache
from django.conf import settings


class Command(BaseCommand):
    help = 'Reset the ESPN API circuit breaker'

    def handle(self, *args, **options):
        # Clear the circuit breaker state from cache
        cache.delete(settings.REDIS_KEY_CIRCUIT_BREAKER)
        
        self.stdout.write(
            self.style.SUCCESS('Circuit breaker has been reset')
        )
        self.stdout.write(
            'The next API call will attempt to reconnect to ESPN.'
        )

