"""
Scoring service for grading picks and updating member statistics.
Handles complex scoring logic including ATS with/without hooks, straight-up picks, and key pick bonuses.
"""
import logging
from decimal import Decimal, InvalidOperation
from typing import Tuple, Optional, List, Dict
from math import ceil

from django.db import transaction
from django.db.models import Sum, Count, Q, F, Max
from ..models import Game, Pick, League, LeagueRules, MemberWeek, MemberSeason, Week, LeagueGame, LeagueMembership

logger = logging.getLogger(__name__)


def round_to_half(value: Decimal) -> Decimal:
    """Round a decimal value up to the nearest 0.5."""
    if isinstance(value, (int, float)):
        value = Decimal(str(value))
    # Multiply by 2, round up, divide by 2
    return (value * 2).quantize(Decimal('1')) / 2


def is_pick_correct(
    pick: Pick,
    game: Game,
    league_rules: LeagueRules,
    league_game: Optional[LeagueGame] = None,
) -> Optional[bool]:
    """
    Determine if a pick is correct based on scoring rules.
    Returns True if correct, False if incorrect, None if it's a tie (no scoring).
    
    Args:
        pick: The Pick object to grade
        game: The finished Game object
        league_rules: The LeagueRules for the league
        league_game: Optional LeagueGame (avoids an extra query when already loaded)
    
    Returns:
        True/False for win/loss, None for tie
    """
    if not game.is_final or game.home_score is None or game.away_score is None:
        return None
    
    if league_game is None:
        try:
            league_game = LeagueGame.objects.get(league=pick.league, game=game)
        except LeagueGame.DoesNotExist:
            return None
    
    actual_margin = Decimal(game.home_score - game.away_score)
    
    if league_rules.against_the_spread_enabled:
        # Scoring is based on ATS
        locked_spread = league_game.locked_home_spread
        if locked_spread is None:
            logger.warning(
                "No locked spread for league_game %s (league=%s, game=%s); treating pick as tie.",
                league_game.id,
                league_game.league_id,
                league_game.game_id,
            )
            return None
        try:
            spread = Decimal(str(locked_spread))
        except (TypeError, InvalidOperation):
            logger.error(
                "Invalid locked spread value '%s' for league_game %s (league=%s, game=%s); treating pick as tie.",
                locked_spread,
                league_game.id,
                league_game.league_id,
                league_game.game_id,
            )
            return None
        
        # Apply force hooks if enabled
        if league_rules.force_hooks:
            spread = round_to_half(spread)
        
        # Check for tie (no hook enforcement and exact spread match)
        if not league_rules.force_hooks and actual_margin == -spread:
            return None  # Tie

        # Determine home team cover
        home_covered = actual_margin > -spread
        
        # Determine if pick was correct
        if pick.picked_team_id == game.home_team_id:
            return home_covered
        else:
            return not home_covered
    else:
        # Scoring is based on straight-up winner
        if pick.picked_team_id == game.home_team_id:
            return actual_margin > 0
        else:
            return actual_margin < 0


def calculate_pick_points(pick: Pick, is_correct: Optional[bool], league_rules: LeagueRules) -> int:
    """
    Calculate points earned for a pick.
    
    Args:
        pick: The Pick object
        is_correct: Whether the pick was correct (None = push/ungraded → 0 points)
        league_rules: The LeagueRules for the league
    
    Returns:
        Points earned (0 if incorrect or push)
    """
    if is_correct is not True:
        return 0
    
    points = league_rules.points_per_correct_pick
    
    # Add key pick bonus if applicable
    if pick.is_key_pick and league_rules.key_picks_enabled:
        points += league_rules.key_pick_extra_points
    
    return points


def resolve_total_points_tiebreak(week_picks) -> tuple:
    """
    Resolve Total Points tiebreaker fields from a user's final week picks.

    Looks up the week's total-points pick (not the game currently being graded),
    so later non-TB finals do not wipe guess/actual/diff.

    Returns:
        (points_guess, points_actual, tiebreak_abs_diff)
    """
    total_pts_pick = (
        week_picks.filter(is_total_points_game=True)
        .select_related("game")
        .order_by("id")
        .first()
    )
    if not total_pts_pick or total_pts_pick.points_guess is None:
        return None, None, None

    points_guess = total_pts_pick.points_guess
    game = total_pts_pick.game
    if game is None or game.home_score is None or game.away_score is None:
        return points_guess, None, None

    points_actual = game.home_score + game.away_score
    return points_guess, points_actual, abs(points_guess - points_actual)


def calculate_tiebreaker_value(member_week: MemberWeek, league_rules: LeagueRules) -> tuple:
    """
    Sort key used after points for week ranking (higher is better).

    Cascade:
      1. League tiebreaker method
      2. Correct picks
      3. Key picks

    Players only share a rank when this full tuple matches.
    """
    return _tiebreak_cascade(
        league_rules,
        correct=member_week.correct,
        correct_key=member_week.correct_key,
        tiebreak_abs_diff=member_week.tiebreak_abs_diff,
    )


def _tiebreak_cascade(
    league_rules: LeagueRules,
    *,
    correct: int,
    correct_key: int,
    tiebreak_abs_diff=None,
) -> tuple:
    """
    (tiebreaker_method, correct, correct_key) — all higher-is-better.

    Total Points uses negated abs diff (closer guess ranks higher).
    Missing Total Points data ranks below any resolved guess, then
    falls through to correct / key among others missing data.
    """
    tiebreaker = league_rules.tiebreaker if league_rules else 0

    if tiebreaker == 1:  # Correct Key Picks
        method_val = correct_key
    elif tiebreaker == 2:  # Total Points
        if tiebreak_abs_diff is None:
            method_val = float("-inf")
        else:
            method_val = -tiebreak_abs_diff
    elif tiebreaker == 3:  # Correct Picks
        method_val = correct
    else:  # None — skip method, fall through to correct then key
        method_val = 0

    return (method_val, correct, correct_key)


def assign_ranks_for_week(member_weeks: List[MemberWeek], league_rules: LeagueRules) -> Dict[int, int]:
    """
    Assign ranks to member weeks with proper tiebreaker handling.

    Ranking cascade:
      1. Points (higher is better)
      2. League tiebreaker method
      3. Correct picks
      4. Key picks
    Equal on the full cascade → same competition rank (next rank skipped).
    """
    if not member_weeks:
        return {}

    def sort_key(member_week):
        return (member_week.points, calculate_tiebreaker_value(member_week, league_rules))

    sorted_weeks = sorted(member_weeks, key=sort_key, reverse=True)

    rank_map = {}
    current_rank = 1
    previous_key = None

    for index, member_week in enumerate(sorted_weeks):
        key = sort_key(member_week)
        if previous_key is not None and key != previous_key:
            current_rank = index + 1
        rank_map[member_week.id] = current_rank
        previous_key = key

    return rank_map


def assign_ranks_for_season(member_seasons: List[MemberSeason], league_rules: LeagueRules) -> Dict[int, Dict[str, int]]:
    """
    Assign ranks to member seasons with proper tiebreaker handling.
    Calculates both full season ranks and adjusted ranks (with drops).

    Same cascade as weeks: points → league tiebreaker → correct → key picks.
    Total Points is weekly-only, so on season it is a no-op and standings
    fall through to correct then key picks.
    """
    if not member_seasons:
        return {}

    def get_adjusted_stats(member_season):
        adjusted_points = member_season.points - member_season.points_dropped
        adjusted_correct = member_season.correct - member_season.correct_dropped
        adjusted_correct_key = member_season.correct_key - member_season.correct_key_dropped
        return adjusted_points, adjusted_correct, adjusted_correct_key

    def calculate_ranks_for_stats(member_seasons_list, use_full_stats=True):
        def sort_key(member_season):
            if use_full_stats:
                points = member_season.points
                correct = member_season.correct
                correct_key = member_season.correct_key
            else:
                points, correct, correct_key = get_adjusted_stats(member_season)
            # Season has no cumulative total-points diff; cascade still applies.
            return (
                points,
                _tiebreak_cascade(
                    league_rules,
                    correct=correct,
                    correct_key=correct_key,
                    tiebreak_abs_diff=None,
                ),
            )

        sorted_seasons = sorted(member_seasons_list, key=sort_key, reverse=True)

        rank_map = {}
        current_rank = 1
        previous_key = None

        for index, member_season in enumerate(sorted_seasons):
            key = sort_key(member_season)
            if previous_key is not None and key != previous_key:
                current_rank = index + 1
            rank_map[member_season.id] = current_rank
            previous_key = key

        return rank_map

    full_rank_map = calculate_ranks_for_stats(member_seasons, use_full_stats=True)

    if league_rules and league_rules.drop_weeks > 0:
        adjusted_rank_map = calculate_ranks_for_stats(member_seasons, use_full_stats=False)
    else:
        adjusted_rank_map = {ms.id: 0 for ms in member_seasons}

    result = {}
    for member_season in member_seasons:
        result[member_season.id] = {
            'rank': full_rank_map.get(member_season.id, 0),
            'rank_with_drops': adjusted_rank_map.get(member_season.id, 0)
        }

    return result


@transaction.atomic
def update_member_week_for_game(game: Game) -> int:
    """
    Update MemberWeek records for a finished game.
    Called when a game is marked as is_final.
    
    Returns the number of MemberWeek records updated.
    """
    if not game.is_final:
        return 0
    
    updated_count = 0
    
    # Get all leagues that have this game
    league_games = LeagueGame.objects.filter(
        game=game, 
        is_active=True
    ).select_related('league')
    
    if not league_games.exists():
        return 0
    
    # Get the week
    if not game.week:
        return 0
    
    for league_game in league_games:
        league = league_game.league
        
        try:
            league_rules = LeagueRules.objects.get(league=league, season=game.season)
        except LeagueRules.DoesNotExist:
            logger.warning(f"No rules found for league {league.id} season {game.season.id}")
            continue
        
        # Get all picks for this game in this league
        picks = Pick.objects.filter(
            league=league,
            game=game
        ).select_related('user')
        
        for pick in picks:
            # Grade the pick (always overwrite so spread changes regrade cleanly)
            is_correct = is_pick_correct(pick, game, league_rules, league_game=league_game)
            if pick.is_correct != is_correct:
                pick.is_correct = is_correct
                pick.save(update_fields=['is_correct'])
            
            # Get or create MemberWeek
            member_week, created = MemberWeek.objects.get_or_create(
                league=league,
                week=game.week,
                user=pick.user
            )
            
            # Stats from all picks on final games this week.
            # is_correct=None on a final game means push/tie (not "ungraded").
            user_picks = Pick.objects.filter(
                league=league,
                user=pick.user,
                game__week=game.week,
                game__is_final=True,
            )
            
            correct_count = user_picks.filter(is_correct=True).count()
            incorrect_count = user_picks.filter(is_correct=False).count()
            ties_count = user_picks.filter(is_correct__isnull=True).count()
            
            # Count key picks correct
            key_picks_correct = user_picks.filter(
                is_key_pick=True,
                is_correct=True
            ).count()
            
            # Calculate total points for the week
            total_points = 0
            for week_pick in user_picks:
                total_points += calculate_pick_points(week_pick, week_pick.is_correct, league_rules)
            
            # Total Points TB must be resolved from the week's TB pick every time
            # a game finals — otherwise later non-TB games wipe guess/diff to None.
            points_guess = None
            points_actual = None
            tiebreak_abs_diff = None
            if league_rules.tiebreaker == 2:
                points_guess, points_actual, tiebreak_abs_diff = resolve_total_points_tiebreak(
                    user_picks
                )

            # Update MemberWeek
            member_week.picks_made = user_picks.count()
            member_week.correct = correct_count
            member_week.incorrect = incorrect_count
            member_week.ties = ties_count
            member_week.correct_key = key_picks_correct
            member_week.points = total_points
            member_week.points_guess = points_guess
            member_week.points_actual = points_actual
            member_week.tiebreak_abs_diff = tiebreak_abs_diff
            member_week.save()
            
            updated_count += 1
        
        # After updating all picks for this league/week, calculate ranks and update MemberSeason
        week_member_weeks = MemberWeek.objects.filter(
            league=league,
            week=game.week,
            user_id__in=LeagueMembership.objects.filter(league=league, is_active=True).values_list('user_id', flat=True),
        )
        
        if week_member_weeks.exists():
            # Calculate and assign ranks
            rank_map = assign_ranks_for_week(list(week_member_weeks), league_rules)
            for member_week in week_member_weeks:
                if member_week.id in rank_map:
                    member_week.rank = rank_map[member_week.id]
                    member_week.save(update_fields=['rank'])
        
        update_member_season_for_league(league, game.season)
    
    logger.debug(
        "Updated %s MemberWeek records for game %s",
        updated_count,
        game.id,
    )
    return updated_count


@transaction.atomic
def update_member_season_for_league(league: League, season) -> int:
    """
    Update all MemberSeason records for a league/season by aggregating MemberWeek data.
    Implements drop_weeks logic to exclude worst performing weeks from season standings.
    
    Returns the number of MemberSeason records updated.
    """
    updated_count = 0
    
    # Get all members of this league
    from django.contrib.auth.models import User
    from ..models import LeagueGame
    
    try:
        league_rules = LeagueRules.objects.get(league=league, season=season)
    except LeagueRules.DoesNotExist:
        logger.warning(f"No rules found for league {league.id} season {season.id}")
        return 0
    
    members = LeagueMembership.objects.filter(league=league, is_active=True).values_list('user_id', flat=True)
    
    # Get weeks that have finalized games for this league
    weeks_with_finalized_games = set(
        LeagueGame.objects.filter(
            league=league,
            game__season=season,
            game__is_final=True,
            is_active=True
        ).values_list('game__week_id', flat=True).distinct()
    )
    
    for user_id in members:
        member_season, created = MemberSeason.objects.get_or_create(
            league=league,
            season=season,
            user_id=user_id
        )
        
        # Get all MemberWeek records for this user in this league/season
        all_member_weeks = MemberWeek.objects.filter(
            league=league,
            week__season=season,
            user_id=user_id
        ).select_related('week').order_by('week__number')
        
        # Filter to only include weeks that have finalized games
        member_weeks_with_finals = all_member_weeks.filter(
            week_id__in=weeks_with_finalized_games
        )
        
        if not member_weeks_with_finals.exists():
            # Reset if no weeks with finalized games
            member_season.through_week = 0
            member_season.picks_made = 0
            member_season.correct = 0
            member_season.incorrect = 0
            member_season.ties = 0
            member_season.correct_key = 0
            member_season.points = 0
            # Reset dropped week stats
            member_season.points_dropped = 0
            member_season.picks_made_dropped = 0
            member_season.correct_dropped = 0
            member_season.incorrect_dropped = 0
            member_season.ties_dropped = 0
            member_season.correct_key_dropped = 0
        else:
            # First, calculate full season stats from all weeks with finalized games
            full_season_stats = member_weeks_with_finals.aggregate(
                max_week=Max('week__number'),
                total_picks=Sum('picks_made'),
                total_correct=Sum('correct'),
                total_incorrect=Sum('incorrect'),
                total_ties=Sum('ties'),
                total_correct_key=Sum('correct_key'),
                total_points=Sum('points')
            )
            
            # Store full season stats in original fields
            member_season.through_week = full_season_stats['max_week'] or 0
            member_season.picks_made = full_season_stats['total_picks'] or 0
            member_season.correct = full_season_stats['total_correct'] or 0
            member_season.incorrect = full_season_stats['total_incorrect'] or 0
            member_season.ties = full_season_stats['total_ties'] or 0
            member_season.correct_key = full_season_stats['total_correct_key'] or 0
            member_season.points = full_season_stats['total_points'] or 0
            
            # Initialize dropped stats to 0
            member_season.points_dropped = 0
            member_season.picks_made_dropped = 0
            member_season.correct_dropped = 0
            member_season.incorrect_dropped = 0
            member_season.ties_dropped = 0
            member_season.correct_key_dropped = 0
            
            # Apply drop_weeks logic if enabled
            if league_rules.drop_weeks > 0 and len(member_weeks_with_finals) > league_rules.drop_weeks:
                # Convert to list for sorting
                weeks_list = list(member_weeks_with_finals)
                
                # Sort weeks by points (ascending) to identify worst weeks
                # Then by tiebreaker (ascending) as secondary sort based on league rules
                # Ascending = worst first; mirror ranking cascade (points → TB → correct → key)
                def get_week_tiebreaker_key(week):
                    return (
                        week.points,
                        _tiebreak_cascade(
                            league_rules,
                            correct=week.correct,
                            correct_key=week.correct_key,
                            tiebreak_abs_diff=week.tiebreak_abs_diff,
                        ),
                    )

                weeks_list.sort(key=get_week_tiebreaker_key)
                
                # Get the weeks to drop (worst performing)
                weeks_to_drop = weeks_list[:league_rules.drop_weeks]
                
                # Calculate dropped stats from the worst weeks
                dropped_stats = sum((week.picks_made for week in weeks_to_drop), 0), \
                               sum((week.correct for week in weeks_to_drop), 0), \
                               sum((week.incorrect for week in weeks_to_drop), 0), \
                               sum((week.ties for week in weeks_to_drop), 0), \
                               sum((week.correct_key for week in weeks_to_drop), 0), \
                               sum((week.points for week in weeks_to_drop), 0)
                
                member_season.picks_made_dropped = dropped_stats[0]
                member_season.correct_dropped = dropped_stats[1]
                member_season.incorrect_dropped = dropped_stats[2]
                member_season.ties_dropped = dropped_stats[3]
                member_season.correct_key_dropped = dropped_stats[4]
                member_season.points_dropped = dropped_stats[5]
        
        member_season.save()
        updated_count += 1
    
    # Calculate and assign season ranks
    season_member_seasons = MemberSeason.objects.filter(
        league=league,
        season=season,
        user_id__in=LeagueMembership.objects.filter(league=league, is_active=True).values_list('user_id', flat=True),
    )
    
    if season_member_seasons.exists():
        rank_map = assign_ranks_for_season(list(season_member_seasons), league_rules)
        for member_season in season_member_seasons:
            if member_season.id in rank_map:
                ranks = rank_map[member_season.id]
                member_season.rank = ranks['rank']
                member_season.rank_with_drops = ranks['rank_with_drops']
                member_season.save(update_fields=['rank', 'rank_with_drops'])
    
    logger.debug(
        "Updated %s MemberSeason records for league %s season %s",
        updated_count,
        league.id,
        season.id,
    )
    return updated_count


@transaction.atomic
def recalculate_all_member_stats(season) -> dict:
    """
    Recalculate all member statistics for a season.
    Used for catching up after fixes or migrations.
    
    Returns a dict with statistics about the operation.
    """
    stats = {
        'leagues_processed': 0,
        'member_weeks_updated': 0,
        'member_seasons_updated': 0,
        'errors': []
    }
    
    # Get all leagues
    leagues = League.objects.all()
    
    for league in leagues:
        try:
            stats['leagues_processed'] += 1
            
            # Get all weeks for this league in the season
            weeks = Week.objects.filter(season=season)
            
            # Clear existing MemberWeek and MemberSeason for this league/season
            MemberWeek.objects.filter(
                league=league,
                week__season=season
            ).delete()
            
            MemberSeason.objects.filter(
                league=league,
                season=season
            ).delete()
            
            # Get all members
            members = LeagueMembership.objects.filter(league=league, is_active=True)
            
            try:
                league_rules = LeagueRules.objects.get(league=league, season=season)
            except LeagueRules.DoesNotExist:
                logger.warning(f"No rules found for league {league.id} season {season.id}")
                continue
            
            for member in members:
                for week in weeks:
                    # Create MemberWeek
                    member_week = MemberWeek.objects.create(
                        league=league,
                        week=week,
                        user=member.user,
                        picks_made=0,
                        correct=0,
                        incorrect=0,
                        ties=0,
                        correct_key=0,
                        points=0
                    )
                    
                    # Picks on final games this week (None is_correct = push/tie)
                    week_picks = Pick.objects.filter(
                        league=league,
                        user=member.user,
                        game__week=week,
                        game__is_final=True,
                    )
                    
                    if week_picks.exists():
                        # Calculate stats
                        correct_count = week_picks.filter(is_correct=True).count()
                        incorrect_count = week_picks.filter(is_correct=False).count()
                        ties_count = week_picks.filter(is_correct__isnull=True).count()
                        key_correct_count = week_picks.filter(is_key_pick=True, is_correct=True).count()
                        
                        total_points = 0
                        for pick in week_picks:
                            total_points += calculate_pick_points(pick, pick.is_correct, league_rules)
                        
                        points_guess = None
                        points_actual = None
                        tiebreak_abs_diff = None
                        if league_rules.tiebreaker == 2:
                            points_guess, points_actual, tiebreak_abs_diff = (
                                resolve_total_points_tiebreak(week_picks)
                            )

                        member_week.picks_made = week_picks.count()
                        member_week.correct = correct_count
                        member_week.incorrect = incorrect_count
                        member_week.ties = ties_count
                        member_week.correct_key = key_correct_count
                        member_week.points = total_points
                        member_week.points_guess = points_guess
                        member_week.points_actual = points_actual
                        member_week.tiebreak_abs_diff = tiebreak_abs_diff
                        member_week.save()
                        
                        stats['member_weeks_updated'] += 1
                
                # Create/update MemberSeason
                member_season = MemberSeason.objects.create(
                    league=league,
                    season=season,
                    user=member.user,
                    through_week=0,
                    picks_made=0,
                    correct=0,
                    incorrect=0,
                    ties=0,
                    correct_key=0,
                    points=0
                )
                
                # Get weeks that have finalized games for this league
                from ..models import LeagueGame
                weeks_with_finalized_games = set(
                    LeagueGame.objects.filter(
                        league=league,
                        game__season=season,
                        game__is_final=True,
                        is_active=True
                    ).values_list('game__week_id', flat=True).distinct()
                )
                
                # Get all MemberWeek records for this user in this league/season
                all_member_weeks = MemberWeek.objects.filter(
                    league=league,
                    week__season=season,
                    user=member.user
                ).select_related('week').order_by('week__number')
                
                # Filter to only include weeks that have finalized games
                member_weeks_with_finals = all_member_weeks.filter(
                    week_id__in=weeks_with_finalized_games
                )
                
                if member_weeks_with_finals.exists():
                    # First, calculate full season stats from all weeks with finalized games
                    full_season_stats = member_weeks_with_finals.aggregate(
                        max_week=Max('week__number'),
                        total_picks=Sum('picks_made'),
                        total_correct=Sum('correct'),
                        total_incorrect=Sum('incorrect'),
                        total_ties=Sum('ties'),
                        total_correct_key=Sum('correct_key'),
                        total_points=Sum('points')
                    )
                    
                    # Store full season stats in original fields
                    member_season.through_week = full_season_stats['max_week'] or 0
                    member_season.picks_made = full_season_stats['total_picks'] or 0
                    member_season.correct = full_season_stats['total_correct'] or 0
                    member_season.incorrect = full_season_stats['total_incorrect'] or 0
                    member_season.ties = full_season_stats['total_ties'] or 0
                    member_season.correct_key = full_season_stats['total_correct_key'] or 0
                    member_season.points = full_season_stats['total_points'] or 0
                    
                    # Initialize dropped stats to 0
                    member_season.points_dropped = 0
                    member_season.picks_made_dropped = 0
                    member_season.correct_dropped = 0
                    member_season.incorrect_dropped = 0
                    member_season.ties_dropped = 0
                    member_season.correct_key_dropped = 0
                    
                    # Apply drop_weeks logic if enabled
                    if league_rules.drop_weeks > 0 and len(member_weeks_with_finals) > league_rules.drop_weeks:
                        # Convert to list for sorting
                        weeks_list = list(member_weeks_with_finals)
                        
                        # Sort weeks by points (ascending) to identify worst weeks
                        # Then by tiebreaker (ascending) as secondary sort based on league rules
                        # Ascending = worst first; mirror ranking cascade
                        def get_week_tiebreaker_key(week):
                            return (
                                week.points,
                                _tiebreak_cascade(
                                    league_rules,
                                    correct=week.correct,
                                    correct_key=week.correct_key,
                                    tiebreak_abs_diff=week.tiebreak_abs_diff,
                                ),
                            )

                        weeks_list.sort(key=get_week_tiebreaker_key)
                        
                        # Get the weeks to drop (worst performing)
                        weeks_to_drop = weeks_list[:league_rules.drop_weeks]
                        
                        # Calculate dropped stats from the worst weeks
                        dropped_stats = sum((week.picks_made for week in weeks_to_drop), 0), \
                                       sum((week.correct for week in weeks_to_drop), 0), \
                                       sum((week.incorrect for week in weeks_to_drop), 0), \
                                       sum((week.ties for week in weeks_to_drop), 0), \
                                       sum((week.correct_key for week in weeks_to_drop), 0), \
                                       sum((week.points for week in weeks_to_drop), 0)
                        
                        member_season.picks_made_dropped = dropped_stats[0]
                        member_season.correct_dropped = dropped_stats[1]
                        member_season.incorrect_dropped = dropped_stats[2]
                        member_season.ties_dropped = dropped_stats[3]
                        member_season.correct_key_dropped = dropped_stats[4]
                        member_season.points_dropped = dropped_stats[5]
                    
                    member_season.save()
                    stats['member_seasons_updated'] += 1
                else:
                    # No weeks with finalized games - reset all stats
                    member_season.through_week = 0
                    member_season.picks_made = 0
                    member_season.correct = 0
                    member_season.incorrect = 0
                    member_season.ties = 0
                    member_season.correct_key = 0
                    member_season.points = 0
                    member_season.points_dropped = 0
                    member_season.picks_made_dropped = 0
                    member_season.correct_dropped = 0
                    member_season.incorrect_dropped = 0
                    member_season.ties_dropped = 0
                    member_season.correct_key_dropped = 0
                    member_season.save()
                    stats['member_seasons_updated'] += 1
            
            # After all members have their stats calculated, calculate ranks for each week
            for week in weeks:
                # Calculate and assign week ranks for this week across all members
                week_member_weeks = MemberWeek.objects.filter(
                    league=league,
                    week=week,
                    user_id__in=LeagueMembership.objects.filter(league=league, is_active=True).values_list('user_id', flat=True),
                )
                if week_member_weeks.exists():
                    rank_map = assign_ranks_for_week(list(week_member_weeks), league_rules)
                    for member_week in week_member_weeks:
                        if member_week.id in rank_map:
                            member_week.rank = rank_map[member_week.id]
                            member_week.save(update_fields=['rank'])
            
            # Calculate and assign season ranks across all members
            season_member_seasons = MemberSeason.objects.filter(
                league=league,
                season=season,
                user_id__in=LeagueMembership.objects.filter(league=league, is_active=True).values_list('user_id', flat=True),
            )
            if season_member_seasons.exists():
                rank_map = assign_ranks_for_season(list(season_member_seasons), league_rules)
                for member_season in season_member_seasons:
                    if member_season.id in rank_map:
                        ranks = rank_map[member_season.id]
                        member_season.rank = ranks['rank']
                        member_season.rank_with_drops = ranks['rank_with_drops']
                        member_season.save(update_fields=['rank', 'rank_with_drops'])
        
        except Exception as e:
            logger.error(f"Error processing league {league.id}: {e}", exc_info=True)
            stats['errors'].append(f"League {league.id}: {str(e)}")
    
    return stats
