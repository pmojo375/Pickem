from django.apps import AppConfig


class CfbConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cfb'
    
    def ready(self):
        """Import signal handlers when the app is ready."""
        from . import signals  # noqa