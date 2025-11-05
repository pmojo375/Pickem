"""Signal handlers for updating member statistics when games are finalized."""

import logging
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Game
from .services.scoring import update_member_week_for_game

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Game)
def cache_previous_game_state(sender, instance, raw=False, **kwargs):
    """Cache whether the game was already final before this save."""
    if raw:
        instance._was_final = False
        return

    if not instance.pk:
        instance._was_final = False
        return

    try:
        previous_final = sender.objects.only("is_final").get(pk=instance.pk).is_final
    except sender.DoesNotExist:
        previous_final = False

    instance._was_final = previous_final


@receiver(post_save, sender=Game)
def game_finalized(sender, instance, created, update_fields, **kwargs):
    """
    Signal handler to update member statistics when a game is marked as final.
    """
    was_final = getattr(instance, "_was_final", False)
    became_final = instance.is_final and (created or not was_final)

    reverted_to_pending = was_final and not instance.is_final

    if became_final:
        finalization_recorded = True

        if instance.finalized_at:
            finalization_recorded = False
        else:
            timestamp = timezone.now()
            try:
                updated = sender.objects.filter(
                    pk=instance.pk,
                    finalized_at__isnull=True,
                ).update(finalized_at=timestamp)

                if updated:
                    instance.finalized_at = timestamp
                else:
                    finalization_recorded = False
            except Exception as cache_error:  # pragma: no cover - defensive logging
                logger.warning(
                    "Unable to record finalization timestamp for game %s: %s",
                    instance.id,
                    cache_error,
                    exc_info=True,
                )
                finalization_recorded = True

        if not finalization_recorded:
            logger.debug(
                "Skipping duplicate finalization processing for game %s", instance.id
            )
            return

        try:
            logger.info(f"Game {instance.id} marked as final, updating member statistics")
            update_member_week_for_game(instance)
        except Exception as e:
            logger.error(f"Error updating member statistics for game {instance.id}: {e}", exc_info=True)
        
        # Also update team records for the season
        try:
            from cfb.tasks import update_team_records_async
            update_team_records_async.delay(instance.season.year)
            logger.info(f"Queued team records update for season {instance.season.year} after game {instance.id} became final")
        except Exception as e:
            logger.error(f"Error queuing team records update for game {instance.id}: {e}", exc_info=True)
    elif reverted_to_pending:
        try:
            sender.objects.filter(pk=instance.pk).update(finalized_at=None)
            instance.finalized_at = None
            logger.info(
                "Cleared finalization timestamp for game %s after is_final reset", instance.id
            )
        except Exception as cache_error:  # pragma: no cover - defensive logging
            logger.warning(
                "Unable to clear finalization timestamp for game %s: %s",
                instance.id,
                cache_error,
                exc_info=True,
            )
