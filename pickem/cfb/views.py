from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.core.exceptions import ValidationError
from .models import Game, Pick, Team, League, LeagueMembership, LeagueGame, LeagueRules, Season, Ranking, Week
from django.utils import timezone
from . import services
from django.conf import settings


def login_view(request):
    """Custom login view with styled template."""
    if request.user.is_authenticated:
        return redirect('home')
    
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f"Welcome back, {username}! 🏈")
                next_url = request.POST.get('next') or request.GET.get('next') or '/'
                return redirect(next_url)
            else:
                messages.error(request, "Invalid username or password.")
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = AuthenticationForm()
    
    context = {
        'form': form,
        'next': request.GET.get('next', ''),
    }
    return render(request, 'cfb/login.html', context)


def logout_view(request):
    """Custom logout view with styled template."""
    if request.method == 'POST' or request.method == 'GET':
        logout(request)
        return render(request, 'cfb/logout.html')
    return redirect('home')


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
        context = {
            "games_with_picks": [],
            "current_league": None,
            "user_leagues": user_leagues,
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

    # Get league games for this league
    league_games = LeagueGame.objects.filter(
        league=league, 
        is_active=True
    ).select_related("game__home_team", "game__away_team").order_by("game__kickoff")
    
    # Get existing picks for this user in this league
    existing_picks_by_game_id = {
        p.game_id: p 
        for p in Pick.objects.filter(user=request.user, league=league, game__in=[lg.game for lg in league_games])
    }
    
    # Get league rules for key pick limits
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
    
    # Get total points game if tiebreaker is enabled
    total_points_game = None
    total_points_pick = None
    if league_rules and league_rules.tiebreaker == 2:
        # Find the game marked as total points game for this league
        total_points_league_game = LeagueGame.objects.filter(
            league=league,
            is_total_points_game=True,
            is_active=True
        ).select_related('game__home_team', 'game__away_team').first()
        
        if total_points_league_game:
            total_points_game = total_points_league_game.game
            # Get existing pick for this game (which may have total points prediction)
            total_points_pick = Pick.objects.filter(
                user=request.user,
                league=league,
                game=total_points_game
            ).first()
    
    context = {
        "games_with_picks": games_with_picks,
        "current_league": league,
        "user_leagues": user_leagues,
        "league_rules": league_rules,
        "current_key_picks_count": current_key_picks_count,
        "total_points_game": total_points_game,
        "total_points_pick": total_points_pick,
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
    
    # Get league rules for force_hooks setting
    active_season = Season.objects.filter(is_active=True).first()
    league_rules = None
    if active_season:
        league_rules = LeagueRules.objects.filter(league=league, season=active_season).first()
    
    context = {
        "picks_with_league_game": picks_with_league_game,
        "current_league": league,
        "user_leagues": user_leagues,
        "league_rules": league_rules,
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
    
    context = {
        'current_league': league,
        'user_leagues': user_leagues,
        'standings': [],
    }
    
    if league:
        # Get standings for this league
        from django.db.models import Count, Q, Sum, Case, When, IntegerField
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        # Get all members of the league
        members = User.objects.filter(league_memberships__league=league).distinct()
        
        standings = []
        for member in members:
            picks = Pick.objects.filter(user=member, league=league, is_correct__isnull=False)
            total = picks.count()
            wins = picks.filter(is_correct=True).count()
            losses = total - wins
            win_pct = round((wins / total * 100) if total > 0 else 0, 1)
            
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
                'total': total,
                'win_pct': win_pct,
                'points': points,
            })
        
        # Sort by points (descending), then by win_pct
        standings.sort(key=lambda x: (-x['points'], -x['win_pct']))
        
        context['standings'] = standings
    
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
                    league_rules, created = LeagueRules.objects.get_or_create(
                        league=target_league,
                        season=target_season,
                        defaults={
                            'points_per_correct_pick': int(request.POST.get("points_per_correct_pick", 1)),
                            'key_pick_extra_points': int(request.POST.get("key_pick_extra_points", 1)),
                            'against_the_spread_enabled': request.POST.get("against_the_spread_enabled") == "on",
                            'force_hooks': request.POST.get("force_hooks") == "on",
                            'spread_lock_weekday': int(request.POST.get("spread_lock_weekday", 2)),
                            'pickable_games_per_week': int(request.POST.get("pickable_games_per_week", 10)),
                            'picks_per_week': int(request.POST.get("picks_per_week", 0)),
                            'key_picks_enabled': request.POST.get("key_picks_enabled") == "on",
                            'number_of_key_picks': int(request.POST.get("number_of_key_picks", 1)),
                            'tiebreaker': int(request.POST.get("tiebreaker", 0)),
                        }
                    )
                    
                    if not created:
                        # Update existing rules
                        league_rules.points_per_correct_pick = int(request.POST.get("points_per_correct_pick", 1))
                        league_rules.key_pick_extra_points = int(request.POST.get("key_pick_extra_points", 1))
                        league_rules.against_the_spread_enabled = request.POST.get("against_the_spread_enabled") == "on"
                        league_rules.force_hooks = request.POST.get("force_hooks") == "on"
                        league_rules.spread_lock_weekday = int(request.POST.get("spread_lock_weekday", 2))
                        league_rules.pickable_games_per_week = int(request.POST.get("pickable_games_per_week", 10))
                        league_rules.picks_per_week = int(request.POST.get("picks_per_week", 0))
                        league_rules.key_picks_enabled = request.POST.get("key_picks_enabled") == "on"
                        league_rules.number_of_key_picks = int(request.POST.get("number_of_key_picks", 1))
                        league_rules.tiebreaker = int(request.POST.get("tiebreaker", 0))
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
        games = Game.objects.filter(kickoff__range=(start, end)).select_related("home_team", "away_team").order_by("kickoff")
    
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
    
    context = {
        "games_with_selection": games_with_selection,
        "current_league": league,
        "manageable_leagues": manageable_leagues,
        "league_rules": league_rules,
        "all_seasons": all_seasons,
        "active_season": active_season,
        "start": start,
        "end": end,
        "team_rankings": team_rankings,
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

