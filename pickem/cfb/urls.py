from django.urls import path
from django.views.generic import RedirectView
from . import views
from . import api_views

urlpatterns = [
    path('', views.home_view, name='home'),
    # Auth URLs
    path('login/', RedirectView.as_view(pattern_name='account_login', permanent=False), name='login'),
    path('logout/', RedirectView.as_view(pattern_name='account_logout', permanent=False), name='logout'),
    # Main app URLs
    path('picks/', views.picks_view, name='picks'),
    path('live/', views.live_view, name='live'),
    path('standings/', views.standings_view, name='standings'),
    path('settings/', views.settings_view, name='settings'),
    path('settings/new-season/', views.start_new_season_view, name='start_new_season'),
    path('account/', views.account_view, name='account'),
    path('roster/', views.roster_view, name='roster'),
    # League URLs
    path('leagues/', views.leagues_list_view, name='leagues_list'),
    path('leagues/create/', views.league_create_view, name='league_create'),
    path('leagues/join/', views.league_join_by_name_view, name='league_join_by_name'),
    path('invite/<str:token>/', views.personal_invite_view, name='personal_invite'),
    path('invite/<str:token>/signup/', views.personal_invite_signup_view, name='personal_invite_signup'),
    path('invite/<str:token>/set-password/', views.personal_invite_set_password_view, name='personal_invite_set_password'),
    path('leagues/invite/<path:token>/', views.league_invite_view, name='league_invite'),
    path('leagues/opt-in/<path:token>/', views.league_opt_in_view, name='league_opt_in'),
    path('leagues/<int:league_id>/', views.league_detail_view, name='league_detail'),
    path('leagues/<int:league_id>/join/', views.league_join_view, name='league_join'),
    path('leagues/<int:league_id>/invite/rotate/', views.league_rotate_invite_view, name='league_rotate_invite'),
    path('leagues/<int:league_id>/join-password/', views.league_change_join_password_view, name='league_change_join_password'),
    path('leagues/<int:league_id>/close/', views.league_close_view, name='league_close'),
    path('leagues/<int:league_id>/reopen/', views.league_reopen_view, name='league_reopen'),
    path('leagues/<int:league_id>/open-season/', views.league_open_for_season_view, name='league_open_for_season'),
    path('leagues/<int:league_id>/email-opt-in/', views.league_email_opt_in_view, name='league_email_opt_in'),
    path('leagues/<int:league_id>/email-invite/', views.league_email_invite_view, name='league_email_invite'),
    path('leagues/<int:league_id>/activate/', views.league_self_activate_view, name='league_self_activate'),
    path('leagues/<int:league_id>/delete/', views.league_delete_view, name='league_delete'),
    path('leagues/<int:league_id>/leave/', views.league_leave_view, name='league_leave'),
    path('leagues/<int:league_id>/members/<int:membership_id>/status/', views.league_member_status_view, name='league_member_status'),
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


