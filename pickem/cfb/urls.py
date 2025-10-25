from django.urls import path
from . import views
from . import api_views

urlpatterns = [
    path('', views.home_view, name='home'),
    # Auth URLs
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    # Main app URLs
    path('picks/', views.picks_view, name='picks'),
    path('live/', views.live_view, name='live'),
    path('standings/', views.standings_view, name='standings'),
    path('settings/', views.settings_view, name='settings'),
    path('roster/', views.roster_view, name='roster'),
    # League URLs
    path('leagues/', views.leagues_list_view, name='leagues_list'),
    path('leagues/create/', views.league_create_view, name='league_create'),
    path('leagues/<int:league_id>/', views.league_detail_view, name='league_detail'),
    path('leagues/<int:league_id>/join/', views.league_join_view, name='league_join'),
    path('leagues/<int:league_id>/leave/', views.league_leave_view, name='league_leave'),
    # App utility endpoints (avoid Django admin path)
    path('cfb-admin/import-schedule/', views.admin_import_schedule, name='admin_import_schedule'),
    path('cfb-admin/update-live/', views.admin_update_live, name='admin_update_live'),
    # User-accessible utilities
    path('update-live-scores/', views.update_live_scores, name='update_live_scores'),
    
    # Public API endpoints for game data
    path('api/games/', api_views.games_list, name='api_games_list'),
    path('api/games/<int:game_id>/', api_views.game_detail, name='api_game_detail'),
    path('api/games/<int:game_id>/spread-history/', api_views.game_spread_history, name='api_game_spread_history'),
    path('api/games/live/', api_views.live_games, name='api_live_games'),
    path('api/games/upcoming/', api_views.upcoming_games, name='api_upcoming_games'),
    path('api/system/status/', api_views.system_status, name='api_system_status'),
]


