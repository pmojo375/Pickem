"""
Scoring service for grading picks and updating member statistics.
Handles complex scoring logic including ATS with/without hooks, straight-up picks, and key pick bonuses.
"""
import logging
from decimal import Decimal
from typing import Tuple, Optional, List, Dict
from math import ceil

from django.db import transaction
from django.db.models import Sum, Count, Q, F, Max
from ..models import Game, Pick, League, LeagueRules, MemberWeek, MemberSeason, Week, LeagueGame

logger = logging.getLogger(__name__)


def round_to_half(value: Decimal) -> Decimal:
    """Round a decimal value up to the nearest 0.5."""
    if isinstance(value, (int, float)):
        value = Decimal(str(value))
    # Multiply by 2, round up, divide by 2
    return (value * 2).quantize(Decimal('1')) / 2


def is_pick_correct(pick: Pick, game: Game, league_rules: LeagueRules) -> Optional[bool]:
    """
    Determine if a pick is correct based on scoring rules.
    Returns True if correct, False if incorrect, None if it's a tie (no scoring).
    
    Args:
        pick: The Pick object to grade
        game: The finished Game object
        league_rules: The LeagueRules for the league
    
    Returns:
        True/False for win/loss, None for tie
    """
    if not game.is_final or game.home_score is None or game.away_score is None:
        return None
    
    # Get the LeagueGame to check locked spread
    try:
        league_game = LeagueGame.objects.get(league=pick.league, game=game)
    except LeagueGame.DoesNotExist:
        return None
    
    actual_margin = game.home_score - game.away_score
    
    if league_rules.against_the_spread_enabled:
        # Scoring is based on ATS
        spread = Decimal(str(league_game.locked_home_spread))
        
        # Apply force hooks if enabled
        if league_rules.force_hooks:
            spread = round_to_half(spread)
        
        # Determine home team cover
        home_covered = actual_margin > -spread
        
        # Check for tie (no hook enforcement and exact spread match)
        if not league_rules.force_hooks and actual_margin == -spread:
            return None  # Tie
        
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


def calculate_pick_points(pick: Pick, is_correct: bool, league_rules: LeagueRules) -> int:
    """
    Calculate points earned for a pick.
    
    Args:
        pick: The Pick object
        is_correct: Whether the pick was correct
        league_rules: The LeagueRules for the league
    
    Returns:
        Points earned (0 if incorrect)
    """
    if not is_correct:
        return 0
    
    points = league_rules.points_per_correct_pick
    
    # Add key pick bonus if applicable
    if pick.is_key_pick and league_rules.key_picks_enabled:
        points += league_rules.key_pick_extra_points
    
    return points


def calculate_tiebreaker_value(member_week: MemberWeek, league_rules: LeagueRules) -> tuple:
    """
    Calculate the tiebreaker value for a MemberWeek based on league rules.
    
    Returns:
        Tuple of (primary_value, secondary_value) for sorting
        Higher values should rank higher (for points, correct, etc.)
        For total-points diff, LOWER is better, so we negate it
    """
    tiebreaker = league_rules.tiebreaker
    
    if tiebreaker == 1:  # Correct Key Picks
        return (member_week.correct_key, member_week.points)
    elif tiebreaker == 2:  # Total Points - closer to actual is better (lower diff is better)
        # Negate the diff so lower values sort higher
        diff = member_week.tiebreak_abs_diff if member_week.tiebreak_abs_diff is not None else float('inf')
        # If no tiebreak data, use worst possible (infinitely high diff)
        # For sorting, negate so that lower diff (closer guess) ranks higher
        primary = -diff if diff != float('inf') else -float('inf')
        return (primary, member_week.correct_key)
    elif tiebreaker == 3:  # Correct Picks
        return (member_week.correct, member_week.points)
    else:  # None (0) - just use points as primary
        return (member_week.points, member_week.correct)


def assign_ranks_for_week(member_weeks: List[MemberWeek], league_rules: LeagueRules) -> Dict[int, int]:
    """
    Assign ranks to member weeks with proper tiebreaker handling.
    If members have the same rank, the next rank is skipped.
    
    Args:
        member_weeks: List of MemberWeek objects for a league/week
        league_rules: The LeagueRules to determine tiebreaker method
    
    Returns:
        Dict mapping member_week.id to rank
    """
    if not member_weeks:
        return {}
    
    # Sort by points (descending) and then by tiebreaker
    sorted_weeks = sorted(
        member_weeks,
        key=lambda x: (calculate_tiebreaker_value(x, league_rules)),
        reverse=True
    )
    
    rank_map = {}
    current_rank = 1
    previous_tiebreaker = None
    
    for member_week in sorted_weeks:
        current_tiebreaker = calculate_tiebreaker_value(member_week, league_rules)
        
        # If tiebreaker values differ, assign the next rank
        if previous_tiebreaker is not None and current_tiebreaker != previous_tiebreaker:
            # Count how many were assigned the same rank to skip properly
            same_rank_count = len([id for id, r in rank_map.items() if r == current_rank - 1])
            if same_rank_count > 1:
                current_rank += same_rank_count - 1
            current_rank += 1
        
        rank_map[member_week.id] = current_rank
        previous_tiebreaker = current_tiebreaker
    
    return rank_map


def assign_ranks_for_season(member_seasons: List[MemberSeason], league_rules: LeagueRules) -> Dict[int, int]:
    """
    Assign ranks to member seasons with proper tiebreaker handling.
    If members have the same rank, the next rank is skipped.
    
    Args:
        member_seasons: List of MemberSeason objects for a league/season
        league_rules: The LeagueRules to determine tiebreaker method
    
    Returns:
        Dict mapping member_season.id to rank
    """
    if not member_seasons:
        return {}
    
    # Sort by points (descending) and then by tiebreaker
    sorted_seasons = sorted(
        member_seasons,
        key=lambda x: (x.points, x.correct_key if league_rules.tiebreaker == 1 else x.correct),
        reverse=True
    )
    
    rank_map = {}
    current_rank = 1
    previous_points = None
    previous_tiebreaker = None
    
    for member_season in sorted_seasons:
        # Get tiebreaker value
        if league_rules.tiebreaker == 1:  # Correct Key Picks
            tiebreaker_value = member_season.correct_key
        elif league_rules.tiebreaker == 3:  # Correct Picks
            tiebreaker_value = member_season.correct
        else:  # None or Total Points
            tiebreaker_value = member_season.points
        
        # If points or tiebreaker differ, assign next rank
        if previous_points is not None and (member_season.points != previous_points or tiebreaker_value != previous_tiebreaker):
            current_rank += 1
        
        rank_map[member_season.id] = current_rank
        previous_points = member_season.points
        previous_tiebreaker = tiebreaker_value
    
    return rank_map


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
            # Grade the pick
            is_correct = is_pick_correct(pick, game, league_rules)
            
            # Get or create MemberWeek
            member_week, created = MemberWeek.objects.get_or_create(
                league=league,
                week=game.week,
                user=pick.user
            )
            
            # Update pick in database if not already graded
            if pick.is_correct is None:
                pick.is_correct = is_correct
                pick.save(update_fields=['is_correct'])
            
            # Recalculate member week stats
            user_picks = Pick.objects.filter(
                league=league,
                user=pick.user,
                game__week=game.week,
                is_correct__isnull=False
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
            
            # Calculate tiebreaker data if applicable (Total Points tiebreaker)
            points_guess = None
            points_actual = None
            tiebreak_abs_diff = None
            
            if league_rules.tiebreaker == 2 and league_game.is_total_points_game:
                # Get this user's points guess for this game
                if pick.is_total_points_game and pick.points_guess is not None:
                    points_guess = pick.points_guess
                
                # Calculate actual total points if game has scores
                if game.home_score is not None and game.away_score is not None:
                    points_actual = game.home_score + game.away_score
                    
                    # Calculate absolute difference
                    if points_guess is not None:
                        tiebreak_abs_diff = abs(points_guess - points_actual)
            
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
            week=game.week
        )
        
        if week_member_weeks.exists():
            # Calculate and assign ranks
            rank_map = assign_ranks_for_week(list(week_member_weeks), league_rules)
            for member_week in week_member_weeks:
                if member_week.id in rank_map:
                    member_week.rank = rank_map[member_week.id]
                    member_week.save(update_fields=['rank'])
        
        update_member_season_for_league(league, game.season)
    
    logger.info(f"Updated {updated_count} MemberWeek records for game {game.id}")
    return updated_count


@transaction.atomic
def update_member_season_for_league(league: League, season) -> int:
    """
    Update all MemberSeason records for a league/season by aggregating MemberWeek data.
    
    Returns the number of MemberSeason records updated.
    """
    updated_count = 0
    
    # Get all members of this league
    from django.contrib.auth.models import User
    from ..models import LeagueMembership
    
    try:
        league_rules = LeagueRules.objects.get(league=league, season=season)
    except LeagueRules.DoesNotExist:
        logger.warning(f"No rules found for league {league.id} season {season.id}")
        return 0
    
    members = LeagueMembership.objects.filter(league=league).values_list('user_id', flat=True)
    
    for user_id in members:
        member_season, created = MemberSeason.objects.get_or_create(
            league=league,
            season=season,
            user_id=user_id
        )
        
        # Aggregate stats from all MemberWeek records
        member_weeks = MemberWeek.objects.filter(
            league=league,
            week__season=season,
            user_id=user_id
        )
        
        if not member_weeks.exists():
            # Reset if no weeks
            member_season.through_week = 0
            member_season.picks_made = 0
            member_season.correct = 0
            member_season.incorrect = 0
            member_season.ties = 0
            member_season.correct_key = 0
            member_season.points = 0
        else:
            # Aggregate the stats
            stats = member_weeks.aggregate(
                max_week=Max('week__number'),
                total_picks=Sum('picks_made'),
                total_correct=Sum('correct'),
                total_incorrect=Sum('incorrect'),
                total_ties=Sum('ties'),
                total_correct_key=Sum('correct_key'),
                total_points=Sum('points')
            )
            
            member_season.through_week = stats['max_week'] or 0
            member_season.picks_made = stats['total_picks'] or 0
            member_season.correct = stats['total_correct'] or 0
            member_season.incorrect = stats['total_incorrect'] or 0
            member_season.ties = stats['total_ties'] or 0
            member_season.correct_key = stats['total_correct_key'] or 0
            member_season.points = stats['total_points'] or 0
        
        member_season.save()
        updated_count += 1
    
    # Calculate and assign season ranks
    season_member_seasons = MemberSeason.objects.filter(
        league=league,
        season=season
    )
    
    if season_member_seasons.exists():
        rank_map = assign_ranks_for_season(list(season_member_seasons), league_rules)
        for member_season in season_member_seasons:
            if member_season.id in rank_map:
                member_season.rank = rank_map[member_season.id]
                member_season.save(update_fields=['rank'])
    
    logger.info(f"Updated {updated_count} MemberSeason records for league {league.id} season {season.id}")
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
            from ..models import LeagueMembership
            members = LeagueMembership.objects.filter(league=league)
            
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
                    
                    # Get all picks for this user/week/league that are graded
                    week_picks = Pick.objects.filter(
                        league=league,
                        user=member.user,
                        game__week=week,
                        is_correct__isnull=False
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
                        
                        # Calculate tiebreaker data if applicable (Total Points tiebreaker)
                        points_guess = None
                        points_actual = None
                        tiebreak_abs_diff = None
                        
                        if league_rules.tiebreaker == 2:
                            # Find any total-points games for this week
                            total_pts_picks = week_picks.filter(is_total_points_game=True)
                            if total_pts_picks.exists():
                                # Get the user's first points guess (usually only one per user per game type)
                                first_guess = total_pts_picks.first()
                                if first_guess.points_guess is not None:
                                    points_guess = first_guess.points_guess
                                
                                # Find the game and get actual total
                                game = first_guess.game
                                if game.home_score is not None and game.away_score is not None:
                                    points_actual = game.home_score + game.away_score
                                    if points_guess is not None:
                                        tiebreak_abs_diff = abs(points_guess - points_actual)
                        
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
                
                # Calculate and assign week ranks
                week_member_weeks = MemberWeek.objects.filter(
                    league=league,
                    week__season=season,
                    user=member.user
                )
                if week_member_weeks.exists():
                    rank_map = assign_ranks_for_week(list(week_member_weeks), league_rules)
                    for member_week in week_member_weeks:
                        if member_week.id in rank_map:
                            member_week.rank = rank_map[member_week.id]
                            member_week.save(update_fields=['rank'])
                
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
                
                # Aggregate from member weeks
                member_weeks = MemberWeek.objects.filter(
                    league=league,
                    week__season=season,
                    user=member.user
                )
                
                if member_weeks.exists():
                    agg_stats = member_weeks.aggregate(
                        max_week=Max('week__number'),
                        total_picks=Sum('picks_made'),
                        total_correct=Sum('correct'),
                        total_incorrect=Sum('incorrect'),
                        total_ties=Sum('ties'),
                        total_key_correct=Sum('correct_key'),
                        total_points=Sum('points')
                    )
                    
                    member_season.through_week = agg_stats['max_week'] or 0
                    member_season.picks_made = agg_stats['total_picks'] or 0
                    member_season.correct = agg_stats['total_correct'] or 0
                    member_season.incorrect = agg_stats['total_incorrect'] or 0
                    member_season.ties = agg_stats['total_ties'] or 0
                    member_season.correct_key = agg_stats['total_key_correct'] or 0
                    member_season.points = agg_stats['total_points'] or 0
                    member_season.save()
                    
                    stats['member_seasons_updated'] += 1
            
            # Calculate and assign season ranks
            season_member_seasons = MemberSeason.objects.filter(
                league=league,
                season=season
            )
            if season_member_seasons.exists():
                rank_map = assign_ranks_for_season(list(season_member_seasons), league_rules)
                for member_season in season_member_seasons:
                    if member_season.id in rank_map:
                        member_season.rank = rank_map[member_season.id]
                        member_season.save(update_fields=['rank'])
        
        except Exception as e:
            logger.error(f"Error processing league {league.id}: {e}", exc_info=True)
            stats['errors'].append(f"League {league.id}: {str(e)}")
    
    return stats
