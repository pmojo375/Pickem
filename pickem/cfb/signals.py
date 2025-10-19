"""
Signal handlers for updating member statistics when games are finalized.
"""
import logging
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Game
from .services.scoring import update_member_week_for_game

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Game)
def game_finalized(sender, instance, created, update_fields, **kwargs):
    """
    Signal handler to update member statistics when a game is marked as final.
    """
    # Only process if is_final was changed to True
    if update_fields is None:
        # If update_fields is None, all fields were updated
        should_process = instance.is_final
    else:
        should_process = 'is_final' in update_fields and instance.is_final
    
    if should_process and instance.is_final:
        try:
            logger.info(f"Game {instance.id} marked as final, updating member statistics")
            update_member_week_for_game(instance)
        except Exception as e:
            logger.error(f"Error updating member statistics for game {instance.id}: {e}", exc_info=True)
