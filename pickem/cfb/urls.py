from django.urls import path
from . import views

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
    # app utility endpoints (avoid Django admin path)
    path('cfb-admin/import-schedule/', views.admin_import_schedule, name='admin_import_schedule'),
    path('cfb-admin/update-odds/', views.admin_update_odds, name='admin_update_odds'),
    path('cfb-admin/update-live/', views.admin_update_live, name='admin_update_live'),
    # User-accessible utilities
    path('update-live-scores/', views.update_live_scores, name='update_live_scores'),
]


