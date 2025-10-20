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
    
    Ranking rules:
    1. Primary sort: Points (higher is better)
    2. Secondary sort: Tiebreaker value from league rules (if points tie)
    3. If tiebreaker also matches, assign same rank
    4. When multiple members share a rank, the next rank is skipped accordingly
       (e.g., if two members tie for 2nd place, the next is 4th, not 3rd)
    
    Args:
        member_weeks: List of MemberWeek objects for a league/week
        league_rules: The LeagueRules to determine tiebreaker method
    
    Returns:
        Dict mapping member_week.id to rank
    """
    if not member_weeks:
        return {}
    
    # Sort by points (descending) and then by tiebreaker value (descending)
    sorted_weeks = sorted(
        member_weeks,
        key=lambda x: (x.points, calculate_tiebreaker_value(x, league_rules)),
        reverse=True
    )
    
    rank_map = {}
    current_rank = 1
    previous_points = None
    previous_tiebreaker = None
    
    for index, member_week in enumerate(sorted_weeks):
        current_points = member_week.points
        current_tiebreaker = calculate_tiebreaker_value(member_week, league_rules)
        
        # Determine if this is a new ranking position
        # A new rank happens when either points OR tiebreaker value changes
        if previous_points is not None and (
            current_points != previous_points or 
            current_tiebreaker != previous_tiebreaker
        ):
            # Assign the next available rank (which accounts for skipped ranks from ties)
            current_rank = index + 1
        
        rank_map[member_week.id] = current_rank
        previous_points = current_points
        previous_tiebreaker = current_tiebreaker
    
    return rank_map


def assign_ranks_for_season(member_seasons: List[MemberSeason], league_rules: LeagueRules) -> Dict[int, Dict[str, int]]:
    """
    Assign ranks to member seasons with proper tiebreaker handling.
    Calculates both full season ranks and adjusted ranks (with drops).
    If members have the same rank, the next rank is skipped.
    
    Args:
        member_seasons: List of MemberSeason objects for a league/season
        league_rules: The LeagueRules to determine tiebreaker method
    
    Returns:
        Dict mapping member_season.id to {'rank': full_rank, 'rank_with_drops': adjusted_rank}
    """
    if not member_seasons:
        return {}
    
    # Helper function to get adjusted stats (full season minus dropped weeks)
    def get_adjusted_stats(member_season):
        adjusted_points = member_season.points - member_season.points_dropped
        adjusted_correct = member_season.correct - member_season.correct_dropped
        adjusted_correct_key = member_season.correct_key - member_season.correct_key_dropped
        return adjusted_points, adjusted_correct, adjusted_correct_key
    
    # Helper function to get tiebreaker value
    def get_tiebreaker_value(points, correct, correct_key, league_rules):
        if league_rules.tiebreaker == 1:  # Correct Key Picks
            return correct_key
        elif league_rules.tiebreaker == 3:  # Correct Picks
            return correct
        else:  # None or Total Points
            return correct
    
    def calculate_ranks_for_stats(member_seasons_list, use_full_stats=True):
        """Calculate ranks for either full stats or adjusted stats"""
        if use_full_stats:
            # Sort by full season stats
            def sort_key(member_season):
                correct = member_season.correct
                correct_key = member_season.correct_key
                tiebreaker = get_tiebreaker_value(member_season.points, correct, correct_key, league_rules)
                return (member_season.points, tiebreaker)
        else:
            # Sort by adjusted stats
            def sort_key(member_season):
                adjusted_points, adjusted_correct, adjusted_correct_key = get_adjusted_stats(member_season)
                tiebreaker = get_tiebreaker_value(adjusted_points, adjusted_correct, adjusted_correct_key, league_rules)
                return (adjusted_points, tiebreaker)
        
        sorted_seasons = sorted(member_seasons_list, key=sort_key, reverse=True)
        
        rank_map = {}
        current_rank = 1
        previous_points = None
        previous_tiebreaker = None
        
        for index, member_season in enumerate(sorted_seasons):
            if use_full_stats:
                points = member_season.points
                correct = member_season.correct
                correct_key = member_season.correct_key
            else:
                points, correct, correct_key = get_adjusted_stats(member_season)
            
            tiebreaker = get_tiebreaker_value(points, correct, correct_key, league_rules)
            
            # Determine if this is a new ranking position
            # A new rank happens when either points OR tiebreaker value changes
            if previous_points is not None and (points != previous_points or tiebreaker != previous_tiebreaker):
                # Assign the next available rank (which accounts for skipped ranks from ties)
                current_rank = index + 1
            
            rank_map[member_season.id] = current_rank
            previous_points = points
            previous_tiebreaker = tiebreaker
        
        return rank_map
    
    # Calculate both types of ranks
    full_rank_map = calculate_ranks_for_stats(member_seasons, use_full_stats=True)
    
    # For adjusted ranks, check if drops are enabled
    if league_rules and league_rules.drop_weeks > 0:
        adjusted_rank_map = calculate_ranks_for_stats(member_seasons, use_full_stats=False)
    else:
        # No drops, so adjusted ranks should be 0 or same as full ranks
        adjusted_rank_map = {ms.id: 0 for ms in member_seasons}
    
    # Combine the results
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
    Implements drop_weeks logic to exclude worst performing weeks from season standings.
    
    Returns the number of MemberSeason records updated.
    """
    updated_count = 0
    
    # Get all members of this league
    from django.contrib.auth.models import User
    from ..models import LeagueMembership, LeagueGame
    
    try:
        league_rules = LeagueRules.objects.get(league=league, season=season)
    except LeagueRules.DoesNotExist:
        logger.warning(f"No rules found for league {league.id} season {season.id}")
        return 0
    
    members = LeagueMembership.objects.filter(league=league).values_list('user_id', flat=True)
    
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
                def get_week_tiebreaker_key(week):
                    if league_rules.tiebreaker == 1:  # Correct Key Picks
                        return (week.points, week.correct_key)
                    elif league_rules.tiebreaker == 3:  # Correct Picks
                        return (week.points, week.correct)
                    else:  # Default to correct picks
                        return (week.points, week.correct)
                
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
        season=season
    )
    
    if season_member_seasons.exists():
        rank_map = assign_ranks_for_season(list(season_member_seasons), league_rules)
        for member_season in season_member_seasons:
            if member_season.id in rank_map:
                ranks = rank_map[member_season.id]
                member_season.rank = ranks['rank']
                member_season.rank_with_drops = ranks['rank_with_drops']
                member_season.save(update_fields=['rank', 'rank_with_drops'])
    
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
                        def get_week_tiebreaker_key(week):
                            if league_rules.tiebreaker == 1:  # Correct Key Picks
                                return (week.points, week.correct_key)
                            elif league_rules.tiebreaker == 3:  # Correct Picks
                                return (week.points, week.correct)
                            else:  # Default to correct picks
                                return (week.points, week.correct)
                        
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
                    week=week
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
                season=season
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
