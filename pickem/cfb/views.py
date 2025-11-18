from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.core.exceptions import ValidationError
from django.db.models import Q
from .models import Game, Pick, Team, League, LeagueMembership, LeagueGame, LeagueRules, Season, Ranking, Week, MemberSeason, MemberWeek
from django.utils import timezone
from . import services
from django.conf import settings

def home_view(request):
    context = {}
    
    if request.user.is_authenticated:
        # Get user's leagues
        user_leagues = League.objects.filter(memberships__user=request.user).distinct()
        
        # Get league from query params or use first league
        league_id = request.GET.get('league_id')
        if league_id:
            league = League.objects.filter(pk=league_id, memberships__user=request.user).first()
        else:
            membership = LeagueMembership.objects.filter(user=request.user).first()
            league = membership.league if membership else None
        
        if league:
            # Get user stats for this league
            from django.utils import timezone
            from datetime import timedelta
            
            # Get current week and its date range
            current_week = services.schedule.get_current_week()
            
            # Picks made this week
            week_picks_count = 0
            if current_week:
                start, end = services.schedule.get_week_datetime_range(current_week)
                week_picks_count = Pick.objects.filter(
                    user=request.user,
                    league=league,
                    game__kickoff__range=(start, end)
                ).count()
            
            # Total correct picks
            total_picks = Pick.objects.filter(user=request.user, league=league, is_correct__isnull=False)
            correct_picks = total_picks.filter(is_correct=True).count()
            total_picks_count = total_picks.count()
            win_rate = round((correct_picks / total_picks_count * 100) if total_picks_count > 0 else 0, 1)
            
            # User ranking in league (by correct picks)
            from django.db.models import Count, Q
            rankings = Pick.objects.filter(
                league=league, 
                is_correct__isnull=False
            ).values('user').annotate(
                correct_count=Count('id', filter=Q(is_correct=True))
            ).order_by('-correct_count')
            
            user_rank = None
            for idx, rank in enumerate(rankings, 1):
                if rank['user'] == request.user.id:
                    user_rank = idx
                    break
            
            context.update({
                'current_league': league,
                'user_leagues': user_leagues,
                'week_picks_count': week_picks_count,
                'win_rate': win_rate,
                'user_rank': user_rank,
                'total_players': league.memberships.count(),
            })
    
    return render(request, "cfb/home.html", context)


@login_required
def picks_view(request):
    # Get league from query params or use user's first league
    league_id = request.GET.get('league_id')
    if league_id:
        league = League.objects.filter(pk=league_id, memberships__user=request.user).first()
    else:
        # Get user's first league
        membership = LeagueMembership.objects.filter(user=request.user).first()
        league = membership.league if membership else None
    
    # Get all user's leagues for the selector
    user_leagues = League.objects.filter(memberships__user=request.user).distinct()
    
    if not league:
        # No league - show message instead of redirecting
        current_week = services.schedule.get_current_week()
        context = {
            "games_with_picks": [],
            "current_league": None,
            "user_leagues": user_leagues,
            "current_week": current_week,
        }
        return render(request, "cfb/picks.html", context)
    
    if request.method == "POST":
        # Process all picks from the form
        saved_count = 0
        errors = []
        
        # Get league from form
        form_league_id = request.POST.get("league_id")
        if form_league_id:
            league = get_object_or_404(League, pk=form_league_id, memberships__user=request.user)
        
        # Find all game IDs in the POST data (format: game_123_id)
        game_ids = []
        for key in request.POST.keys():
            if key.startswith("game_") and key.endswith("_id"):
                game_id = request.POST.get(key)
                if game_id:
                    game_ids.append(game_id)
        
        # Get league rules for key pick validation
        from django.utils import timezone
        from datetime import timedelta
        
        # Get current week and its date range
        current_week = services.schedule.get_current_week()
        
        # Get active season and league rules
        active_season = Season.objects.filter(is_active=True).first()
        league_rules = None
        if active_season:
            league_rules = LeagueRules.objects.filter(league=league, season=active_season).first()
            if not league_rules:
                # Create default rules if none exist
                league_rules = LeagueRules.objects.create(league=league, season=active_season)
        
        # Count current key picks for this week (excluding games being updated)
        current_key_picks = Pick.objects.none()
        if current_week:
            start, end = services.schedule.get_week_datetime_range(current_week)
            current_key_picks = Pick.objects.filter(
                user=request.user,
                league=league,
                is_key_pick=True,
                game__kickoff__range=(start, end)
            ).exclude(game_id__in=game_ids)
        current_key_picks_count = current_key_picks.count()
        
        # Count new key picks being submitted
        new_key_picks_count = 0
        for game_id in game_ids:
            is_key_pick = request.POST.get(f"game_{game_id}_is_key_pick") == "on"
            if is_key_pick:
                new_key_picks_count += 1
        
        # Validate key pick limit
        if league_rules and league_rules.key_picks_enabled:
            total_key_picks = current_key_picks_count + new_key_picks_count
            if total_key_picks > league_rules.number_of_key_picks:
                errors.append(f"You can only select {league_rules.number_of_key_picks} key pick{'s' if league_rules.number_of_key_picks != 1 else ''} per week. You currently have {current_key_picks_count} and are trying to add {new_key_picks_count} more.")
        
        # Process each game's pick
        for game_id in game_ids:
            picked_team_id = request.POST.get(f"game_{game_id}_picked_team")
            is_key_pick = request.POST.get(f"game_{game_id}_is_key_pick") == "on"
            
            # Only process if a team was actually selected
            if picked_team_id:
                try:
                    # Verify game is selected for this league
                    league_game = LeagueGame.objects.get(league=league, game_id=game_id, is_active=True)
                    game = league_game.game
                    picked_team = Team.objects.get(pk=picked_team_id)
                    
                    # Check if game has started - prevent editing picks for started games
                    if game.has_started():
                        errors.append(f"Cannot change picks for {game.away_team.name} @ {game.home_team.name} - game has already started")
                        continue
                    
                    # Validate team is in the game
                    if picked_team_id_not_in_game(picked_team_id=picked_team.id, game=game):
                        errors.append(f"Invalid team selection for {game.away_team.name} @ {game.home_team.name}")
                        continue
                    
                    # Save the pick
                    Pick.objects.update_or_create(
                        user=request.user,
                        league=league,
                        game=game,
                        defaults={"picked_team": picked_team, "is_key_pick": is_key_pick},
                    )
                    saved_count += 1
                except (LeagueGame.DoesNotExist, Game.DoesNotExist, Team.DoesNotExist):
                    errors.append(f"Invalid game or team selection")
                    continue
        
        # Handle total points prediction if tiebreaker is enabled
        if league_rules and league_rules.tiebreaker == 2:
            total_points_guess = request.POST.get("total_points_guess")
            
            # Find the game marked as total points game for this league
            total_points_league_game = LeagueGame.objects.filter(
                league=league,
                is_total_points_game=True,
                is_active=True
            ).first()
            
            if total_points_league_game and total_points_guess:
                try:
                    points_guess_value = int(total_points_guess)
                    game = total_points_league_game.game
                    
                    # Check if game has started
                    if not game.has_started():
                        # Find existing pick for this game (may or may not exist yet)
                        pick = Pick.objects.filter(
                            user=request.user,
                            league=league,
                            game=game
                        ).first()
                        
                        if pick:
                            # Update existing pick with total points guess
                            pick.points_guess = points_guess_value
                            pick.is_total_points_game = True
                            pick.save(update_fields=["points_guess", "is_total_points_game"])
                        else:
                            # Create a new pick with just the total points guess
                            # Use home team as placeholder (user hasn't made team pick yet)
                            Pick.objects.create(
                                user=request.user,
                                league=league,
                                game=game,
                                picked_team=game.home_team,
                                points_guess=points_guess_value,
                                is_total_points_game=True
                            )
                except (ValueError, TypeError):
                    errors.append("Invalid total points prediction value.")
        
        # Show results
        if saved_count > 0:
            messages.success(request, f"Successfully saved {saved_count} pick{'s' if saved_count != 1 else ''}! 🏈")
        if errors:
            for error in errors:
                messages.error(request, error)
        if saved_count == 0 and not errors:
            messages.warning(request, "No picks were selected. Click on teams to make your picks!")
        
        return redirect(f"/picks/?league_id={league.id}")

    # Get league rules for key pick limits
    from django.utils import timezone
    from datetime import timedelta
    
    # Get current week and its date range
    current_week = services.schedule.get_current_week()
    
    # Get league games for this league - filter by current week only
    league_games = LeagueGame.objects.filter(
        league=league, 
        is_active=True
    ).select_related("game__home_team", "game__away_team")
    
    # Filter to only show games from the current week
    if current_week:
        start, end = services.schedule.get_week_datetime_range(current_week)
        league_games = league_games.filter(game__kickoff__range=(start, end))
    
    league_games = league_games.order_by("game__kickoff")
    
    # Get existing picks for this user in this league
    existing_picks_by_game_id = {
        p.game_id: p 
        for p in Pick.objects.filter(user=request.user, league=league, game__in=[lg.game for lg in league_games])
    }
    
    # Get active season and league rules
    active_season = Season.objects.filter(is_active=True).first()
    league_rules = None
    if active_season:
        league_rules = LeagueRules.objects.filter(league=league, season=active_season).first()
        if not league_rules:
            # Create default rules if none exist
            league_rules = LeagueRules.objects.create(league=league, season=active_season)
    
    # Count current key picks for this week
    current_key_picks_count = 0
    if current_week:
        start, end = services.schedule.get_week_datetime_range(current_week)
        current_key_picks_count = Pick.objects.filter(
            user=request.user,
            league=league,
            is_key_pick=True,
            game__kickoff__range=(start, end)
        ).count()
    
    # Combine league_games with picks
    games_with_picks = [(lg, existing_picks_by_game_id.get(lg.game.id)) for lg in league_games]
    
    # Get total points game if tiebreaker is enabled - only if it's in the current week
    total_points_game = None
    total_points_pick = None
    if league_rules and league_rules.tiebreaker == 2 and current_week:
        start, end = services.schedule.get_week_datetime_range(current_week)
        # Find the game marked as total points game for this league in the current week
        total_points_league_game = LeagueGame.objects.filter(
            league=league,
            is_total_points_game=True,
            is_active=True,
            game__kickoff__range=(start, end)
        ).select_related('game__home_team', 'game__away_team').first()
        
        if total_points_league_game:
            total_points_game = total_points_league_game.game
            # Get existing pick for this game (which may have total points prediction)
            total_points_pick = Pick.objects.filter(
                user=request.user,
                league=league,
                game=total_points_game
            ).first()
    
    # Get AP poll rankings for teams (current week)
    team_rankings = {}
    if active_season and current_week:
        # Fetch AP poll rankings for current week
        rankings = Ranking.objects.filter(
            season=active_season,
            week=current_week,
            poll='AP Top 25'
        ).select_related('team')

        # Create a dict mapping team_id to rank
        team_rankings = {r.team_id: r.rank for r in rankings}

    # Get team records for all teams in the games
    team_records = {}
    game_teams = set()
    if active_season:
        # Get all unique teams from the league games
        for lg, _ in games_with_picks:
            game_teams.add(lg.game.home_team_id)
            game_teams.add(lg.game.away_team_id)

        if game_teams:
            # Fetch records for all teams in the current season
            teams_with_records = Team.objects.filter(
                season=active_season,
                id__in=game_teams
            )

            # Create a dict mapping team_id to (wins, losses) tuple
            team_records = {
                team.id: (team.record_wins, team.record_losses)
                for team in teams_with_records
            }

    # Get team stats for all teams in the games
    from .models import TeamStat
    team_stats = {}
    if active_season and game_teams:
        # Fetch all stats for teams in the games
        stats_queryset = TeamStat.objects.filter(
            season=active_season,
            team_id__in=game_teams
        ).select_related('team')
        
        # Organize stats by team_id
        for stat in stats_queryset:
            if stat.team_id not in team_stats:
                team_stats[stat.team_id] = {}
            team_stats[stat.team_id][stat.stat] = stat.value

    context = {
        "games_with_picks": games_with_picks,
        "current_league": league,
        "user_leagues": user_leagues,
        "league_rules": league_rules,
        "current_key_picks_count": current_key_picks_count,
        "total_points_game": total_points_game,
        "total_points_pick": total_points_pick,
        "team_rankings": team_rankings,
        "team_records": team_records,
        "team_stats": team_stats,
        "current_week": current_week,
    }
    return render(request, "cfb/picks.html", context)


@login_required
def live_view(request):
    # Get league from query params or use user's first league
    league_id = request.GET.get('league_id')
    if league_id:
        league = League.objects.filter(pk=league_id, memberships__user=request.user).first()
    else:
        # Get user's first league
        membership = LeagueMembership.objects.filter(user=request.user).first()
        league = membership.league if membership else None
    
    # Get all user's leagues for the selector
    user_leagues = League.objects.filter(memberships__user=request.user).distinct()
    
    if not league:
        # No league - show message instead of redirecting
        context = {
            "picks_with_league_game": [],
            "current_league": None,
            "user_leagues": user_leagues,
        }
        return render(request, "cfb/live.html", context)
    
    # Show picks for selected games in the current week window
    current_week = services.schedule.get_current_week()
    
    # Get league games that are active
    league_games = []
    picks = []
    
    if current_week:
        start, end = services.schedule.get_week_datetime_range(current_week)
        league_games = LeagueGame.objects.filter(
            league=league,
            is_active=True,
            game__kickoff__range=(start, end)
        ).values_list('game_id', flat=True)
        
        # Get picks for these games
        picks = Pick.objects.filter(
            user=request.user,
            league=league,
            game_id__in=league_games,
            game__kickoff__range=(start, end)
        ).select_related("game__home_team", "game__away_team", "picked_team")
    
    # Get league_game data for spreads
    league_games_dict = {
        lg.game_id: lg 
        for lg in LeagueGame.objects.filter(league=league, game_id__in=[p.game_id for p in picks])
    }
    
    # Attach league_game to each pick for template access to locked spreads
    picks_with_league_game = []
    for pick in picks:
        league_game = league_games_dict.get(pick.game_id)
        picks_with_league_game.append((pick, league_game))
    
    # Sort games by status priority: Live > Final > Scheduled
    # Within each group, sort by kickoff time (earliest first)
    def get_game_status(game):
        """Helper to determine game status"""
        if not game.is_final and game.quarter and game.quarter > 0:
            return 'live'
        elif game.is_final:
            return 'final'
        else:
            return 'scheduled'
    
    def get_game_sort_key(pick_tuple):
        pick, league_game = pick_tuple
        game = pick.game
        status = get_game_status(game)
        
        # Status priority: live=0, final=1, scheduled=2
        status_priority = {'live': 0, 'final': 1, 'scheduled': 2}[status]
        
        # Return tuple: (status_priority, kickoff_time)
        return (status_priority, game.kickoff)
    
    picks_with_league_game.sort(key=get_game_sort_key)
    
    # Add section markers for template
    picks_with_sections = []
    prev_status = None
    for pick, league_game in picks_with_league_game:
        current_status = get_game_status(pick.game)
        
        # Add section marker if status changed
        if current_status != prev_status:
            picks_with_sections.append({
                'is_section': True,
                'status': current_status
            })
            prev_status = current_status
        
        # Add the actual game
        picks_with_sections.append({
            'is_section': False,
            'pick': pick,
            'league_game': league_game
        })
    
    # Get league rules for force_hooks setting
    active_season = Season.objects.filter(is_active=True).first()
    league_rules = None
    if active_season:
        league_rules = LeagueRules.objects.filter(league=league, season=active_season).first()
    
    # Get AP poll rankings for teams (current week)
    team_rankings = {}
    if active_season and current_week:
        # Fetch AP poll rankings for current week
        rankings = Ranking.objects.filter(
            season=active_season,
            week=current_week,
            poll='AP Top 25'
        ).select_related('team')
        
        # Create a dict mapping team_id to rank
        team_rankings = {r.team_id: r.rank for r in rankings}
    
    # Get team records for all teams in the games
    team_records = {}
    if active_season:
        # Get all unique teams from the picks
        game_teams = set()
        for pick in picks:
            game_teams.add(pick.game.home_team_id)
            game_teams.add(pick.game.away_team_id)
        
        # Get team records for these teams
        teams_with_records = Team.objects.filter(
            season=active_season,
            id__in=game_teams
        )
        
        # Create a dict mapping team_id to (wins, losses) tuple
        team_records = {
            team.id: (team.record_wins, team.record_losses)
            for team in teams_with_records
        }
    
    context = {
        "picks_with_league_game": picks_with_league_game,  # Keep for backward compatibility
        "picks_with_sections": picks_with_sections,  # New organized structure
        "current_league": league,
        "user_leagues": user_leagues,
        "league_rules": league_rules,
        "team_rankings": team_rankings,
        "team_records": team_records,
    }
    return render(request, "cfb/live.html", context)


@login_required
def standings_view(request):
    # Get user's leagues
    user_leagues = League.objects.filter(memberships__user=request.user).distinct()
    
    # Get league from query params or use first league
    league_id = request.GET.get('league_id')
    if league_id:
        league = League.objects.filter(pk=league_id, memberships__user=request.user).first()
    else:
        membership = LeagueMembership.objects.filter(user=request.user).first()
        league = membership.league if membership else None
    
    # Check if user wants to see full standings (not adjusted for dropped weeks)
    show_full_standings = request.GET.get('full', 'false').lower() == 'true'
    
    # Check if user wants to see week standings or league picks
    week_id = request.GET.get('week')
    show_league_picks = request.GET.get('league_picks', 'false').lower() == 'true'
    show_unstarted_picks = request.GET.get('show_unstarted', 'false').lower() == 'true'
    selected_week = None
    
    context = {
        'current_league': league,
        'user_leagues': user_leagues,
        'standings': [],
        'show_full_standings': show_full_standings,
        'selected_week': selected_week,
        'available_weeks': [],
        'show_week_standings': False,  # Will be set later based on logic
        'show_league_picks': show_league_picks,
        'show_unstarted_picks': show_unstarted_picks,
        'league_picks_data': None,  # Will be set if showing league picks
        'is_league_manager': False,  # Will be set based on user role
        'key_picks_enabled': False,  # Will be set based on league rules
    }
    
    if league:
        # Get active season
        active_season = Season.objects.filter(is_active=True).first()
        
        if active_season:
            # Get league rules to check drop_weeks setting
            try:
                league_rules = LeagueRules.objects.get(league=league, season=active_season)
            except LeagueRules.DoesNotExist:
                league_rules = None
            
            # Get available weeks (weeks with live games or all games completed)
            available_weeks = []
            all_weeks = Week.objects.filter(season=active_season).order_by('number')
            
            for week in all_weeks:
                # Check if this week has games in this league
                week_games = Game.objects.filter(
                    week=week,
                    league_selections__league=league,
                    league_selections__is_active=True
                )
                
                if week_games.exists():
                    # Check game status
                    live_games = week_games.filter(is_final=False, home_score__isnull=False)
                    all_final = not week_games.exclude(is_final=True).exists()
                    has_live_or_final = live_games.exists() or all_final
                    
                    if has_live_or_final:
                        available_weeks.append({
                            'week': week,
                            'has_live_games': live_games.exists(),
                            'all_games_final': all_final,
                            'game_count': week_games.count()
                        })
            
            context['available_weeks'] = available_weeks
            
            # Handle 'latest' week parameter or validate week exists
            if week_id == 'latest' and available_weeks:
                # Use the latest available week
                latest_week_data = available_weeks[-1]
                week_id = latest_week_data['week'].id
            elif week_id and available_weeks:
                # Validate that the requested week exists and is available
                try:
                    week_exists = any(w['week'].id == int(week_id) for w in available_weeks)
                    if not week_exists:
                        # Week doesn't exist or not available, default to latest
                        latest_week_data = available_weeks[-1]
                        week_id = latest_week_data['week'].id
                except (ValueError, TypeError):
                    # Invalid week_id, default to latest
                    latest_week_data = available_weeks[-1]
                    week_id = latest_week_data['week'].id
            
            # Handle week standings vs season standings vs league picks
            if week_id:
                # Get week standings
                try:
                    selected_week = Week.objects.get(id=week_id, season=active_season)
                    context['selected_week'] = selected_week
                    
                    if show_league_picks:
                        # Check if user is a league manager (owner or admin)
                        is_manager = False
                        try:
                            membership = LeagueMembership.objects.get(league=league, user=request.user)
                            is_manager = membership.role in ['owner', 'admin']
                        except LeagueMembership.DoesNotExist:
                            pass
                        
                        # Show league picks for this week
                        context['show_league_picks'] = True
                        context['is_league_manager'] = is_manager
                        context['league_picks_data'] = get_league_picks_data(league, selected_week, show_unstarted_picks)
                    else:
                        # Show week standings
                        context['show_week_standings'] = True
                        
                        # Get member weeks for this week
                        member_weeks = MemberWeek.objects.filter(
                            league=league,
                            week=selected_week
                        ).select_related('user', 'week')
                        
                        standings = []
                        # Calculate maximum possible key picks for this week
                        max_key_picks_per_week = league_rules.number_of_key_picks if league_rules and league_rules.key_picks_enabled else 0
                        
                        for member_week in member_weeks:
                            total = member_week.correct + member_week.incorrect + member_week.ties
                            win_pct = round((member_week.correct / total * 100) if total > 0 else 0, 1)
                            
                            # Calculate key pick percentage based on max allowed key picks for this week
                            key_pick_pct = 0
                            if max_key_picks_per_week > 0:
                                # Use the league rule maximum for this week, not actual picks made
                                key_pick_pct = round((member_week.correct_key / max_key_picks_per_week * 100), 1)
                            
                            standings.append({
                                'user': member_week.user,
                                'wins': member_week.correct,
                                'losses': member_week.incorrect,
                                'ties': member_week.ties,
                                'total': total,
                                'picks_made': member_week.picks_made,
                                'win_pct': win_pct,
                                'points': member_week.points,
                                'correct_key': member_week.correct_key,
                                'key_pick_pct': key_pick_pct,
                                'display_rank': member_week.rank or 999,
                            })
                        
                        # Sort by rank (ascending)
                        standings.sort(key=lambda x: x['display_rank'])
                        context['standings'] = standings
                        context['key_picks_enabled'] = league_rules and league_rules.key_picks_enabled
                    
                except Week.DoesNotExist:
                    context['show_week_standings'] = False
                    context['selected_week'] = None
            
            # Default to season standings if no week selected
            if not week_id:
                # Get all member seasons for this league/season
                member_seasons = MemberSeason.objects.filter(
                    league=league,
                    season=active_season
                ).select_related('user')
                
                standings = []
                # Calculate total possible key picks for the season
                max_key_picks_per_week = league_rules.number_of_key_picks if league_rules and league_rules.key_picks_enabled else 0
                
                # Get weeks that have actually had games for this league to calculate max possible key picks
                weeks_with_games = set(
                    Game.objects.filter(
                        season=active_season,
                        league_selections__league=league,
                        league_selections__is_active=True
                    ).values_list('week_id', flat=True).distinct()
                )
                max_total_key_picks = max_key_picks_per_week * len(weeks_with_games) if max_key_picks_per_week > 0 else 0
                
                for member_season in member_seasons:
                    if show_full_standings:
                        # Show full season stats (not adjusted)
                        points = member_season.points
                        correct = member_season.correct
                        correct_key = member_season.correct_key
                        incorrect = member_season.incorrect
                        ties = member_season.ties
                        picks_made = member_season.picks_made
                        total = correct + incorrect + ties
                        display_rank = member_season.rank or 999
                    else:
                        # Show adjusted stats (default - with dropped weeks)
                        points = member_season.points - member_season.points_dropped
                        correct = member_season.correct - member_season.correct_dropped
                        correct_key = member_season.correct_key - member_season.correct_key_dropped
                        incorrect = member_season.incorrect - member_season.incorrect_dropped
                        ties = member_season.ties - member_season.ties_dropped
                        picks_made = member_season.picks_made - member_season.picks_made_dropped
                        total = correct + incorrect + ties
                        # Use rank_with_drops if available and drop_weeks > 0, otherwise use regular rank
                        if league_rules and league_rules.drop_weeks > 0 and member_season.rank_with_drops:
                            display_rank = member_season.rank_with_drops
                        else:
                            display_rank = member_season.rank or 999
                    
                    win_pct = round((correct / total * 100) if total > 0 else 0, 1)
                    
                    # Calculate key pick percentage based on max possible key picks for the season
                    key_pick_pct = 0
                    if max_total_key_picks > 0:
                        # Use the maximum possible key picks according to league rules
                        key_pick_pct = round((correct_key / max_total_key_picks * 100), 1)
                    
                    standings.append({
                        'user': member_season.user,
                        'wins': correct,
                        'losses': incorrect,
                        'ties': ties,
                        'total': total,
                        'picks_made': picks_made,
                        'win_pct': win_pct,
                        'points': points,
                        'correct_key': correct_key,
                        'key_pick_pct': key_pick_pct,
                        'display_rank': display_rank,
                    })
                
                # Sort standings by display rank (ascending)
                standings.sort(key=lambda x: x['display_rank'])
                context['standings'] = standings
            
            context['league_rules'] = league_rules
            context['key_picks_enabled'] = league_rules and league_rules.key_picks_enabled
        else:
            # Fallback to old method if no active season or member seasons
            from django.db.models import Count, Q, Sum, Case, When, IntegerField
            from django.contrib.auth import get_user_model
            User = get_user_model()
            
            # Try to get league rules for fallback case
            fallback_league_rules = None
            if active_season:
                try:
                    fallback_league_rules = LeagueRules.objects.get(league=league, season=active_season)
                except LeagueRules.DoesNotExist:
                    pass
            
            # Get all members of the league
            members = User.objects.filter(league_memberships__league=league).distinct()
            
            # Calculate max possible key picks for fallback case
            max_total_key_picks_fallback = 0
            if fallback_league_rules and fallback_league_rules.key_picks_enabled and active_season:
                # Get weeks that have actually had games for this league
                weeks_with_games_fallback = set(
                    Game.objects.filter(
                        season=active_season,
                        league_selections__league=league,
                        league_selections__is_active=True
                    ).values_list('week_id', flat=True).distinct()
                )
                max_key_picks_per_week_fallback = fallback_league_rules.number_of_key_picks
                max_total_key_picks_fallback = max_key_picks_per_week_fallback * len(weeks_with_games_fallback)
            
            standings = []
            for member in members:
                all_picks = Pick.objects.filter(user=member, league=league)
                picks = Pick.objects.filter(user=member, league=league, is_correct__isnull=False)
                total = picks.count()
                wins = picks.filter(is_correct=True).count()
                losses = picks.filter(is_correct=False).count()
                ties = picks.filter(is_correct=None).count() if picks.filter(is_correct=None).exists() else 0
                picks_made = all_picks.count()
                
                # Calculate key picks
                correct_key = Pick.objects.filter(
                    user=member, 
                    league=league, 
                    is_correct=True,
                    is_key_pick=True
                ).count()
                
                win_pct = round((wins / total * 100) if total > 0 else 0, 1)
                
                # Calculate key pick percentage based on max possible key picks
                key_pick_pct = 0
                if max_total_key_picks_fallback > 0:
                    # Use the maximum possible key picks according to league rules
                    key_pick_pct = round((correct_key / max_total_key_picks_fallback * 100), 1)
                
                # Calculate points (1 for correct, 2 for key pick correct)
                points = Pick.objects.filter(
                    user=member, 
                    league=league, 
                    is_correct=True
                ).aggregate(
                    total_points=Sum(
                        Case(
                            When(is_key_pick=True, then=2),
                            default=1,
                            output_field=IntegerField()
                        )
                    )
                )['total_points'] or 0
                
                standings.append({
                    'user': member,
                    'wins': wins,
                    'losses': losses,
                    'ties': ties,
                    'total': total,
                    'picks_made': picks_made,
                    'win_pct': win_pct,
                    'points': points,
                    'correct_key': correct_key,
                    'key_pick_pct': key_pick_pct,
                })
            
            # Sort by points (descending), then by win_pct
            standings.sort(key=lambda x: (-x['points'], -x['win_pct']))
            
            # Assign ranks for fallback case
            current_rank = 1
            for i, standing in enumerate(standings):
                if i > 0 and standings[i-1]['points'] != standing['points']:
                    current_rank = i + 1
                standing['display_rank'] = current_rank
            
            context['standings'] = standings
            
            # Use the fallback league rules we got earlier
            context['league_rules'] = fallback_league_rules
            context['key_picks_enabled'] = fallback_league_rules and fallback_league_rules.key_picks_enabled
    
    return render(request, "cfb/standings.html", context)


@login_required
def settings_view(request):
    if not request.user.is_staff:
        return redirect("home")
    
    # Get league - allow staff to manage any league, or their own league if owner/admin
    league_id = request.GET.get('league_id')
    if league_id:
        if request.user.is_staff:
            league = League.objects.filter(pk=league_id).first()
        else:
            league = League.objects.filter(pk=league_id, memberships__user=request.user, memberships__role__in=['owner', 'admin']).first()
    else:
        # Get first league where user is owner/admin or staff can see first league
        if request.user.is_staff:
            league = League.objects.first()
        else:
            membership = LeagueMembership.objects.filter(user=request.user, role__in=['owner', 'admin']).first()
            league = membership.league if membership else None
    
    # Get all leagues user can manage
    if request.user.is_staff:
        manageable_leagues = League.objects.all()
    else:
        manageable_leagues = League.objects.filter(memberships__user=request.user, memberships__role__in=['owner', 'admin']).distinct()
    
    if not league:
        # No league - show message instead of redirecting
        context = {
            "games_with_selection": [],
            "current_league": None,
            "manageable_leagues": manageable_leagues,
            "start": None,
            "end": None,
            "cfbd_enabled": bool(settings.CFBD_API_KEY)
        }
        return render(request, "cfb/settings.html", context)
    
    if request.method == "POST":
        action = request.POST.get("do")
        
        if action == "save_league_rules":
            # Get league from form
            form_league_id = request.POST.get("league_id")
            season_id = request.POST.get("season_id")
            
            if form_league_id and season_id:
                if request.user.is_staff:
                    target_league = get_object_or_404(League, pk=form_league_id)
                else:
                    target_league = get_object_or_404(League, pk=form_league_id, memberships__user=request.user, memberships__role__in=['owner', 'admin'])
                
                target_season = get_object_or_404(Season, pk=season_id)
                
                # Get or create league rules for this season
                try:
                    # Parse payout structure data
                    from decimal import Decimal
                    import json
                    
                    entry_fee = request.POST.get("entry_fee", "").strip()
                    entry_fee_value = Decimal(entry_fee) if entry_fee else None
                    
                    weekly_payout_percent = request.POST.get("weekly_payout_percent", "").strip()
                    weekly_payout_percent_value = Decimal(weekly_payout_percent) if weekly_payout_percent else None
                    
                    season_payout_percent = request.POST.get("season_payout_percent", "").strip()
                    season_payout_percent_value = Decimal(season_payout_percent) if season_payout_percent else None
                    
                    # Parse weekly payout structure
                    weekly_payout_structure = {}
                    weekly_spots = request.POST.get("weekly_payout_spots", "").strip()
                    if weekly_spots:
                        try:
                            num_spots = int(weekly_spots)
                            for i in range(1, num_spots + 1):
                                spot_key = f"weekly_spot_{i}_percent"
                                spot_value = request.POST.get(spot_key, "").strip()
                                if spot_value:
                                    weekly_payout_structure[str(i)] = float(spot_value)
                        except (ValueError, TypeError):
                            pass
                    
                    # Parse season payout structure
                    season_payout_structure = {}
                    season_spots = request.POST.get("season_payout_spots", "").strip()
                    if season_spots:
                        try:
                            num_spots = int(season_spots)
                            for i in range(1, num_spots + 1):
                                spot_key = f"season_spot_{i}_percent"
                                spot_value = request.POST.get(spot_key, "").strip()
                                if spot_value:
                                    season_payout_structure[str(i)] = float(spot_value)
                        except (ValueError, TypeError):
                            pass
                    
                    season_payout_last_percent = request.POST.get("season_payout_last_percent", "").strip()
                    season_payout_last_percent_value = Decimal(season_payout_last_percent) if season_payout_last_percent else None
                    
                    league_rules, created = LeagueRules.objects.get_or_create(
                        league=target_league,
                        season=target_season,
                        defaults={
                            'points_per_correct_pick': int(request.POST.get("points_per_correct_pick", 1)),
                            'key_pick_extra_points': int(request.POST.get("key_pick_extra_points", 1)),
                            'drop_weeks': int(request.POST.get("drop_weeks", 0)),
                            'against_the_spread_enabled': request.POST.get("against_the_spread_enabled") == "on",
                            'force_hooks': request.POST.get("force_hooks") == "on",
                            'spread_lock_weekday': int(request.POST.get("spread_lock_weekday", 2)),
                            'pickable_games_per_week': int(request.POST.get("pickable_games_per_week", 10)),
                            'picks_per_week': int(request.POST.get("picks_per_week", 0)),
                            'key_picks_enabled': request.POST.get("key_picks_enabled") == "on",
                            'number_of_key_picks': int(request.POST.get("number_of_key_picks", 1)),
                            'tiebreaker': int(request.POST.get("tiebreaker", 0)),
                            'entry_fee': entry_fee_value,
                            'weekly_payout_percent': weekly_payout_percent_value,
                            'season_payout_percent': season_payout_percent_value,
                            'weekly_payout_structure': weekly_payout_structure,
                            'season_payout_structure': season_payout_structure,
                            'season_payout_last_percent': season_payout_last_percent_value,
                        }
                    )
                    
                    if not created:
                        # Update existing rules
                        league_rules.points_per_correct_pick = int(request.POST.get("points_per_correct_pick", 1))
                        league_rules.key_pick_extra_points = int(request.POST.get("key_pick_extra_points", 1))
                        league_rules.drop_weeks = int(request.POST.get("drop_weeks", 0))
                        league_rules.against_the_spread_enabled = request.POST.get("against_the_spread_enabled") == "on"
                        league_rules.force_hooks = request.POST.get("force_hooks") == "on"
                        league_rules.spread_lock_weekday = int(request.POST.get("spread_lock_weekday", 2))
                        league_rules.pickable_games_per_week = int(request.POST.get("pickable_games_per_week", 10))
                        league_rules.picks_per_week = int(request.POST.get("picks_per_week", 0))
                        league_rules.key_picks_enabled = request.POST.get("key_picks_enabled") == "on"
                        league_rules.number_of_key_picks = int(request.POST.get("number_of_key_picks", 1))
                        league_rules.tiebreaker = int(request.POST.get("tiebreaker", 0))
                        league_rules.entry_fee = entry_fee_value
                        league_rules.weekly_payout_percent = weekly_payout_percent_value
                        league_rules.season_payout_percent = season_payout_percent_value
                        league_rules.weekly_payout_structure = weekly_payout_structure
                        league_rules.season_payout_structure = season_payout_structure
                        league_rules.season_payout_last_percent = season_payout_last_percent_value
                        league_rules.save()
                    
                    action_word = "created" if created else "updated"
                    messages.success(request, f"League rules for '{target_league.name}' ({target_season.year}) have been {action_word} successfully!")
                except (ValueError, TypeError) as e:
                    messages.error(request, f"Invalid input: {e}")
                
                return redirect(f"/settings/?league_id={target_league.id}")
        
        if action == "save_selections":
            # Get league from form
            form_league_id = request.POST.get("league_id")
            if form_league_id:
                if request.user.is_staff:
                    league = get_object_or_404(League, pk=form_league_id)
                else:
                    league = get_object_or_404(League, pk=form_league_id, memberships__user=request.user, memberships__role__in=['owner', 'admin'])
            
            # Process all game selections from the form
            from django.utils import timezone
            lock_spread = request.POST.get("lock_spread") == "on"
            
            # Find all game IDs in the POST data
            game_ids = []
            for key in request.POST.keys():
                if key.startswith("game_") and key.endswith("_id"):
                    game_id = request.POST.get(key)
                    if game_id:
                        game_ids.append(game_id)
            
            # Get league rules to check pick limit
            active_season = Season.objects.filter(is_active=True).first()
            league_rules = None
            if active_season:
                league_rules = LeagueRules.objects.filter(league=league, season=active_season).first()
            
            # Check if against_the_spread is enabled for this league
            ats_enabled = league_rules.against_the_spread_enabled if league_rules else False
            spread_lock_weekday = league_rules.spread_lock_weekday if league_rules else 2
            
            # Count how many games are being selected
            selected_games = []
            for game_id in game_ids:
                is_selected = request.POST.get(f"game_{game_id}_select") == "on"
                if is_selected:
                    selected_games.append(game_id)
            
            # Check pick limit
            if league_rules and league_rules.pickable_games_per_week > 0:
                if len(selected_games) > league_rules.pickable_games_per_week:
                    messages.error(request, f"You can only select up to {league_rules.pickable_games_per_week} games for this league. You selected {len(selected_games)} games.")
                    return redirect(f"/settings/?league_id={league.id}")
            
            selected_count = 0
            locked_count = 0
            deselected_count = 0
            
            # Process each game
            for game_id in game_ids:
                is_selected = request.POST.get(f"game_{game_id}_select") == "on"
                
                try:
                    game = Game.objects.get(pk=game_id)
                    
                    if is_selected:
                        # Create or update LeagueGame
                        league_game, created = LeagueGame.objects.get_or_create(
                            league=league,
                            game=game,
                            defaults={'is_active': True}
                        )
                        
                        # Lock the spread if requested and game has spreads
                        if lock_spread and game.current_home_spread is not None:
                            league_game.locked_home_spread = game.current_home_spread
                            league_game.locked_away_spread = game.current_away_spread
                            league_game.spread_locked_at = timezone.now()
                            league_game.save(update_fields=["locked_home_spread", "locked_away_spread", "spread_locked_at"])
                            locked_count += 1
                        elif ats_enabled and league_game.locked_home_spread is None:
                            # If against_the_spread is enabled and no locked spread yet, apply the spread lock rule
                            # BUT only if we're on or after the lock day - otherwise let the automated task handle it
                            from .models import GameSpread, Week
                            from datetime import timedelta
                            
                            # Get the week for this game
                            week_obj = game.week
                            if week_obj:
                                week_start = week_obj.start_date
                                
                                # Calculate the target lock date (spread_lock_weekday within the week)
                                days_until_lock_day = (spread_lock_weekday - week_start.weekday()) % 7
                                lock_target_date = week_start + timedelta(days=days_until_lock_day)
                                
                                # Only lock if we're AFTER the lock day, or if we're ON the lock day and already have a spread from that day
                                today = timezone.now().date()
                                
                                # Get all spreads for this game ordered by timestamp
                                game_spreads = GameSpread.objects.filter(game=game).order_by('timestamp')
                                
                                if today > lock_target_date and game_spreads.exists():
                                    # We're AFTER the lock day, so lock using tiered logic
                                    spread_to_use = None
                                    
                                    # Try to find spread from the lock target date
                                    for spread in game_spreads:
                                        if spread.timestamp.date() == lock_target_date:
                                            spread_to_use = spread
                                            break
                                    
                                    # If no spread from lock day, find the next spread after lock day
                                    if not spread_to_use:
                                        for spread in game_spreads:
                                            if spread.timestamp.date() > lock_target_date:
                                                spread_to_use = spread
                                                break
                                    
                                    # If still no spread, use the latest one
                                    if not spread_to_use:
                                        spread_to_use = game_spreads.last()
                                    
                                    # Lock the spread
                                    if spread_to_use:
                                        league_game.locked_home_spread = spread_to_use.home_spread
                                        league_game.locked_away_spread = spread_to_use.away_spread
                                        league_game.spread_locked_at = timezone.now()
                                        league_game.save(update_fields=['locked_home_spread', 'locked_away_spread', 'spread_locked_at'])
                                        locked_count += 1
                                        
                                elif today == lock_target_date and game_spreads.exists():
                                    # We're ON the lock day, only lock if we already have a spread from today
                                    spread_from_today = game_spreads.filter(timestamp__date=lock_target_date).first()
                                    if spread_from_today:
                                        league_game.locked_home_spread = spread_from_today.home_spread
                                        league_game.locked_away_spread = spread_from_today.away_spread
                                        league_game.spread_locked_at = timezone.now()
                                        league_game.save(update_fields=['locked_home_spread', 'locked_away_spread', 'spread_locked_at'])
                                        locked_count += 1
                                    # else: It's lock day but no spread from today yet, let automated task handle it
                                # else: Before lock day, leave spread unlocked for now
                        elif not created:
                            # Just ensure it's active
                            league_game.is_active = True
                            league_game.save(update_fields=["is_active"])
                        
                        selected_count += 1
                    else:
                        # Deselect by marking as inactive (don't delete to preserve history)
                        LeagueGame.objects.filter(league=league, game=game).update(is_active=False)
                        deselected_count += 1
                        
                except Game.DoesNotExist:
                    continue
            
            # Handle total points game selection
            total_points_game_id = request.POST.get("total_points_game_id")
            
            # Clear any existing total points game for this league
            old_total_points_games = LeagueGame.objects.filter(league=league, is_total_points_game=True)
            for old_game in old_total_points_games:
                old_game.is_total_points_game = False
                old_game.save(update_fields=["is_total_points_game"])
                
                # Clear is_total_points_game flag from any picks for this game
                Pick.objects.filter(league=league, game=old_game.game, is_total_points_game=True).update(is_total_points_game=False)
            
            # Set the new total points game if one was selected
            if total_points_game_id:
                try:
                    game = Game.objects.get(pk=total_points_game_id)
                    # Verify this game is in the selected games
                    league_game = LeagueGame.objects.get(league=league, game=game, is_active=True)
                    league_game.is_total_points_game = True
                    league_game.save(update_fields=["is_total_points_game"])
                    
                    # Re-enable is_total_points_game flag for any picks that have points_guess for this game
                    # This handles the case where admin changes tiebreaker game and then changes it back
                    Pick.objects.filter(
                        league=league,
                        game=game,
                        points_guess__isnull=False
                    ).update(is_total_points_game=True)
                except (Game.DoesNotExist, LeagueGame.DoesNotExist):
                    # Silently ignore if game not found or not selected
                    pass
            
            # Show results
            if selected_count > 0:
                msg = f"Successfully selected {selected_count} game{'s' if selected_count != 1 else ''} for {league.name}"
                if locked_count > 0:
                    msg += f" (locked spreads for {locked_count})"
                messages.success(request, msg + "! 🏈")
            elif deselected_count > 0:
                messages.info(request, f"Deselected {deselected_count} game(s) for {league.name}.")
            else:
                messages.info(request, "No changes made.")
            
            return redirect(f"/settings/?league_id={league.id}")

    # Get current week and its date range
    current_week = services.schedule.get_current_week()
    start, end = None, None
    games = Game.objects.none()
    
    if current_week:
        start, end = services.schedule.get_week_datetime_range(current_week)
        games = Game.objects.filter(
            kickoff__range=(start, end)
        ).filter(
            Q(home_team__classification='fbs') | Q(away_team__classification='fbs')
        ).select_related("home_team", "away_team").order_by("kickoff")
    
    # Get existing league games for this league that were created within the current week window
    # This ensures we don't show games from previous weeks that might have been selected before
    league_games_dict = {}
    if current_week and games.exists():
        league_games_dict = {
            lg.game_id: lg 
            for lg in LeagueGame.objects.filter(
                league=league, 
                game__in=games, 
                is_active=True,
                selected_at__range=(start, end)
            )
        }
    
    # Combine games with their league_game status
    games_with_selection = [(g, league_games_dict.get(g.id)) for g in games]
    
    # Get all seasons and current league rules
    all_seasons = Season.objects.all().order_by('-year')
    active_season = Season.objects.filter(is_active=True).first()
    
    # Get league rules for active season (or create default)
    league_rules = None
    if active_season:
        league_rules = LeagueRules.objects.filter(league=league, season=active_season).first()
        if not league_rules:
            # Create default rules for this season
            league_rules = LeagueRules.objects.create(
                league=league,
                season=active_season
            )
    
    # Get AP poll rankings for teams (current week)
    team_rankings = {}
    if active_season and current_week:
        # Fetch AP poll rankings for current week
        rankings = Ranking.objects.filter(
            season=active_season,
            week=current_week,
            poll='AP Top 25'
        ).select_related('team')
        
        # Create a dict mapping team_id to rank
        team_rankings = {r.team_id: r.rank for r in rankings}
    
    # Get team records for all teams in the games
    team_records = {}
    if active_season:
        # Get all unique teams from the games
        game_teams = set()
        for game, _ in games_with_selection:
            game_teams.add(game.home_team_id)
            game_teams.add(game.away_team_id)
        
        # Get team records for these teams
        teams_with_records = Team.objects.filter(
            season=active_season,
            id__in=game_teams
        )
        
        # Create a dict mapping team_id to (wins, losses) tuple
        team_records = {
            team.id: (team.record_wins, team.record_losses)
            for team in teams_with_records
        }
    
    # Serialize JSON fields for template
    weekly_payout_structure_json = "{}"
    season_payout_structure_json = "{}"
    if league_rules:
        import json
        if league_rules.weekly_payout_structure:
            weekly_payout_structure_json = json.dumps(league_rules.weekly_payout_structure)
        if league_rules.season_payout_structure:
            season_payout_structure_json = json.dumps(league_rules.season_payout_structure)
    
    # Get league member count for payout calculations
    league_member_count = 0
    if league:
        league_member_count = LeagueMembership.objects.filter(league=league).count()
    
    context = {
        "games_with_selection": games_with_selection,
        "current_league": league,
        "manageable_leagues": manageable_leagues,
        "league_rules": league_rules,
        "all_seasons": all_seasons,
        "active_season": active_season,
        "weekly_payout_structure_json": weekly_payout_structure_json,
        "season_payout_structure_json": season_payout_structure_json,
        "league_member_count": league_member_count,
        "start": start,
        "end": end,
        "team_rankings": team_rankings,
        "team_records": team_records,
        "cfbd_enabled": bool(settings.CFBD_API_KEY)
    }
    return render(request, "cfb/settings.html", context)


@login_required
def roster_view(request):
    # Get user's leagues
    user_leagues = League.objects.filter(memberships__user=request.user).distinct()
    
    # Get league from query params or use first league
    league_id = request.GET.get('league_id')
    if league_id:
        league = League.objects.filter(pk=league_id, memberships__user=request.user).first()
    else:
        membership = LeagueMembership.objects.filter(user=request.user).first()
        league = membership.league if membership else None
    
    context = {
        'current_league': league,
        'user_leagues': user_leagues,
        'roster': [],
    }
    
    if league:
        # Get roster for this league with stats
        from django.db.models import Count, Q
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        # Get all members with their membership info
        memberships = LeagueMembership.objects.filter(league=league).select_related('user').order_by('-role', 'joined_at')
        
        roster = []
        for membership in memberships:
            picks = Pick.objects.filter(user=membership.user, league=league, is_correct__isnull=False)
            total = picks.count()
            wins = picks.filter(is_correct=True).count()
            losses = total - wins
            win_pct = round((wins / total * 100) if total > 0 else 0, 1)
            
            roster.append({
                'membership': membership,
                'user': membership.user,
                'role': membership.role,
                'joined_at': membership.joined_at,
                'wins': wins,
                'losses': losses,
                'total': total,
                'win_pct': win_pct,
            })
        
        context['roster'] = roster
    
    return render(request, "cfb/roster.html", context)


def picked_team_id_not_in_game(picked_team_id: int, game: Game) -> bool:
    return picked_team_id not in (game.home_team_id, game.away_team_id)


def get_league_picks_data(league, week, show_unstarted_picks=False):
    """
    Get league picks data for a specific week.
    Returns data structure for the league picks table.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    # Get all league members
    members = User.objects.filter(league_memberships__league=league).distinct()
    
    # Get games for this week that are selected for this league
    start, end = services.schedule.get_week_datetime_range(week)
    league_games = LeagueGame.objects.filter(
        league=league,
        is_active=True,
        game__week=week,
        game__kickoff__range=(start, end)
    ).select_related('game__home_team', 'game__away_team').order_by('game__kickoff')
    
    # Get all games (not just started/finished ones)
    games = [lg.game for lg in league_games]
    
    # Get all picks for this week and league
    picks = Pick.objects.filter(
        league=league,
        game__in=games
    ).select_related('user', 'picked_team', 'game').order_by('user__username', 'game__kickoff')
    
    # Organize picks by user
    picks_by_user = {}
    for pick in picks:
        if pick.user_id not in picks_by_user:
            picks_by_user[pick.user_id] = []
        picks_by_user[pick.user_id].append(pick)
    
    # Create member data structure
    members_data = []
    for member in members:
        member_picks = picks_by_user.get(member.id, [])
        members_data.append({
            'user': member,
            'picks': member_picks
        })
    
    return {
        'games': games,
        'members': members_data,
        'show_unstarted_picks': show_unstarted_picks
    }


@user_passes_test(lambda u: u.is_staff)
def admin_import_schedule(request):
    """Import schedule for the current season using CFBD API."""
    from .tasks import pull_season_games
    
    # Get active season
    active_season = Season.objects.filter(is_active=True).first()
    if not active_season:
        return JsonResponse({"ok": False, "error": "No active season found"})
    
    # Trigger task to pull ALL games for the season
    pull_season_games(force=True)
    
    # Count total games in the season
    count = Game.objects.filter(season=active_season).count()
    
    return JsonResponse({"ok": True, "imported": count, "season": active_season.year})


@user_passes_test(lambda u: u.is_staff)
def admin_update_live(request):
    updated = services.live.fetch_and_store_live_scores()
    return JsonResponse({"ok": True, "updated": updated})


@login_required
def update_live_scores(request):
    """Allow logged-in users to update live scores from ESPN API."""
    if request.method == "POST":
        league_id = request.POST.get('league_id')
        updated = services.live.fetch_and_store_live_scores()
        
        if updated > 0:
            messages.success(request, f"Updated scores for {updated} game{'s' if updated != 1 else ''}! 🏈")
        else:
            messages.info(request, "No games needed updating at this time.")
        
        # Redirect back to live page with league
        if league_id:
            return redirect(f"/live/?league_id={league_id}")
        return redirect("live")
    
    return redirect("live")


# ============ LEAGUE VIEWS ============

@login_required
def leagues_list_view(request):
    """Show all leagues the user is a member of and all public leagues."""
    user_leagues = League.objects.filter(memberships__user=request.user).distinct()
    all_leagues = League.objects.filter(is_active=True).order_by("-created_at")
    
    context = {
        "user_leagues": user_leagues,
        "all_leagues": all_leagues,
    }
    return render(request, "cfb/leagues_list.html", context)


@login_required
def league_create_view(request):
    """Create a new league."""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()
        
        if not name:
            messages.error(request, "League name is required.")
            return render(request, "cfb/league_create.html", {"name": name, "description": description})
        
        # Check if league name already exists (case-insensitive)
        if League.objects.filter(name__iexact=name).exists():
            messages.error(request, f"A league with the name '{name}' already exists. Please choose a different name.")
            return render(request, "cfb/league_create.html", {"name": name, "description": description})
        
        try:
            league = League.objects.create(
                name=name,
                description=description,
                created_by=request.user
            )
            
            # Automatically add the creator as owner
            LeagueMembership.objects.create(
                league=league,
                user=request.user,
                role="owner"
            )
            
            messages.success(request, f"League '{league.name}' created successfully! 🎉")
            return redirect("league_detail", league_id=league.id)
            
        except ValidationError as e:
            messages.error(request, str(e))
            return render(request, "cfb/league_create.html", {"name": name, "description": description})
    
    return render(request, "cfb/league_create.html")


@login_required
def league_detail_view(request, league_id):
    """View details of a specific league."""
    league = get_object_or_404(League, pk=league_id)
    
    # Check if user is a member
    try:
        membership = LeagueMembership.objects.get(league=league, user=request.user)
        is_member = True
        user_role = membership.role
    except LeagueMembership.DoesNotExist:
        is_member = False
        user_role = None
    
    # Get all members
    memberships = LeagueMembership.objects.filter(league=league).select_related("user").order_by("-role", "joined_at")
    
    context = {
        "league": league,
        "is_member": is_member,
        "user_role": user_role,
        "memberships": memberships,
        "is_owner": user_role == "owner",
        "is_admin": user_role in ["owner", "admin"],
    }
    return render(request, "cfb/league_detail.html", context)


@login_required
def league_join_view(request, league_id):
    """Join a league."""
    league = get_object_or_404(League, pk=league_id)
    
    # Check if already a member
    if LeagueMembership.objects.filter(league=league, user=request.user).exists():
        messages.info(request, f"You are already a member of '{league.name}'.")
        return redirect("league_detail", league_id=league.id)
    
    # Add user to league
    LeagueMembership.objects.create(
        league=league,
        user=request.user,
        role="member"
    )
    
    messages.success(request, f"You have joined '{league.name}'! 🎉")
    return redirect("league_detail", league_id=league.id)


@login_required
def league_leave_view(request, league_id):
    """Leave a league."""
    league = get_object_or_404(League, pk=league_id)
    
    try:
        membership = LeagueMembership.objects.get(league=league, user=request.user)
        
        # Don't allow owner to leave (they should delete the league instead)
        if membership.role == "owner":
            messages.error(request, "League owners cannot leave their league. You can delete it or transfer ownership instead.")
            return redirect("league_detail", league_id=league.id)
        
        membership.delete()
        messages.success(request, f"You have left '{league.name}'.")
        return redirect("leagues_list")
        
    except LeagueMembership.DoesNotExist:
        messages.error(request, "You are not a member of this league.")
        return redirect("leagues_list")

