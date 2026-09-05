"""
Signal handlers for updating member statistics when games are finalized
or league locked spreads change on final games.
"""
import logging
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Game, LeagueGame
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

    if became_final:
        try:
            logger.info(f"Game {instance.id} marked as final, updating member statistics")
            update_member_week_for_game(instance)
        except Exception as e:
            logger.error(f"Error updating member statistics for game {instance.id}: {e}", exc_info=True)

        try:
            from cfb.tasks import queue_team_records_update
            queue_team_records_update(instance.season.year)
        except Exception as e:
            logger.error(f"Error queuing team records update for game {instance.id}: {e}", exc_info=True)


@receiver(pre_save, sender=LeagueGame)
def cache_previous_league_game_spreads(sender, instance, raw=False, **kwargs):
    """Cache prior locked spreads so we can detect changes after save."""
    if raw or not instance.pk:
        instance._previous_locked_home_spread = None
        instance._previous_locked_away_spread = None
        return

    try:
        previous = sender.objects.only(
            "locked_home_spread",
            "locked_away_spread",
        ).get(pk=instance.pk)
        instance._previous_locked_home_spread = previous.locked_home_spread
        instance._previous_locked_away_spread = previous.locked_away_spread
    except sender.DoesNotExist:
        instance._previous_locked_home_spread = None
        instance._previous_locked_away_spread = None


@receiver(post_save, sender=LeagueGame)
def regrade_on_locked_spread_change(sender, instance, created, **kwargs):
    """
    If locked spreads change on a final game, regrade picks for that game.
    """
    if created:
        return

    prev_home = getattr(instance, "_previous_locked_home_spread", None)
    prev_away = getattr(instance, "_previous_locked_away_spread", None)
    spread_changed = (
        prev_home != instance.locked_home_spread
        or prev_away != instance.locked_away_spread
    )
    if not spread_changed:
        return

    game = instance.game
    if not game.is_final:
        return

    try:
        logger.info(
            "Locked spread changed for league_game %s (league=%s, game=%s); regrading",
            instance.id,
            instance.league_id,
            game.id,
        )
        update_member_week_for_game(game)
    except Exception as e:
        logger.error(
            "Error regrading after locked spread change for league_game %s: %s",
            instance.id,
            e,
            exc_info=True,
        )
