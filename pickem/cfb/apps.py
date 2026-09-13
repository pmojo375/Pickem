from django.apps import AppConfig


class CfbConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cfb'
    
    def ready(self):
        """Import signal handlers and configure PostHog when the app is ready."""
        from . import signals  # noqa

        from django.conf import settings

        if getattr(settings, "POSTHOG_ENABLED", False):
            import posthog

            posthog.api_key = settings.POSTHOG_API_KEY
            posthog.host = settings.POSTHOG_HOST
