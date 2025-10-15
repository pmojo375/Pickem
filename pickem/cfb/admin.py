from django.contrib import admin
from .models import Season, Team, Game, GameSpread, Pick, League, LeagueMembership, LeagueGame, LeagueRules, Location, Week, Ranking


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ("year", "name", "is_active", "teams_pulled", "games_pulled")
    list_filter = ("is_active", "teams_pulled", "games_pulled")
    search_fields = ("year", "name")


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "abbreviation", "classification", "conference", "division", "season", "record_display")
    list_filter = ("season", "classification", "conference", "division")
    search_fields = ("name", "nickname", "abbreviation", "cfbd_id", "espn_id", "twitter", "conference")
    
    def record_display(self, obj):
        """Display team record"""
        if obj.record_wins or obj.record_losses:
            return f"{obj.record_wins}-{obj.record_losses}"
        return "-"
    record_display.short_description = "Record"


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = (
        "game_display",
        "week",
        "season_type",
        "kickoff",
        "current_spread_display",
        "score_display",
        "is_final",
    )
    list_filter = ("season", "week", "season_type", "is_final", "neutral_site", "conference_game")
    search_fields = ("home_team__name", "away_team__name", "venue_name")
    autocomplete_fields = ("home_team", "away_team")
    readonly_fields = ("opening_spread_display", "current_spread_display")
    
    def game_display(self, obj):
        """Display game matchup"""
        return f"{obj.away_team.abbreviation or obj.away_team.name} @ {obj.home_team.abbreviation or obj.home_team.name}"
    game_display.short_description = "Game"
    
    def score_display(self, obj):
        """Display score"""
        if obj.away_score is not None and obj.home_score is not None:
            return f"{obj.away_score}-{obj.home_score}"
        return "-"
    score_display.short_description = "Score"
    
    def current_spread_display(self, obj):
        """Display current spread in a readable format"""
        if obj.current_home_spread is not None:
            return f"Home: {obj.current_home_spread:+.1f} / Away: {obj.current_away_spread:+.1f}"
        return "-"
    current_spread_display.short_description = "Current Spread"
    
    def opening_spread_display(self, obj):
        """Display opening spread in a readable format"""
        if obj.opening_home_spread is not None:
            return f"Home: {obj.opening_home_spread:+.1f} / Away: {obj.opening_away_spread:+.1f}"
        return "-"
    opening_spread_display.short_description = "Opening Spread"


@admin.register(GameSpread)
class GameSpreadAdmin(admin.ModelAdmin):
    list_display = ("game", "home_spread", "away_spread", "source", "timestamp")
    list_filter = ("source", "timestamp")
    search_fields = ("game__home_team__name", "game__away_team__name")
    autocomplete_fields = ("game",)
    readonly_fields = ("timestamp",)
    ordering = ("-timestamp",)


@admin.register(Pick)
class PickAdmin(admin.ModelAdmin):
    list_display = ("user", "league", "game", "picked_team", "is_key_pick", "is_correct", "created_at")
    list_filter = ("league", "is_key_pick", "is_correct")
    search_fields = ("user__username", "picked_team__name", "league__name")
    autocomplete_fields = ("league", "game", "picked_team", "user")


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ("name", "created_by", "is_active", "created_at", "member_count")
    list_filter = ("is_active", "created_at")
    search_fields = ("name", "description", "created_by__username")
    readonly_fields = ("created_at",)
    
    def member_count(self, obj):
        """Display the number of members in the league"""
        return obj.memberships.count()
    member_count.short_description = "Members"


@admin.register(LeagueMembership)
class LeagueMembershipAdmin(admin.ModelAdmin):
    list_display = ("league", "user", "role", "joined_at")
    list_filter = ("role", "joined_at")
    search_fields = ("league__name", "user__username")
    autocomplete_fields = ("league", "user")
    readonly_fields = ("joined_at",)


@admin.register(LeagueRules)
class LeagueRulesAdmin(admin.ModelAdmin):
    list_display = (
        "league",
        "season",
        "points_per_correct_pick",
        "key_pick_extra_points",
        "spread_lock_weekday",
        "pickable_games_per_week",
        "key_picks_enabled",
        "updated_at",
    )
    list_filter = ("league", "season", "key_picks_enabled")
    search_fields = ("league__name", "season__year")
    autocomplete_fields = ("league", "season")
    readonly_fields = ("created_at", "updated_at")
    
    fieldsets = (
        ("League & Season", {
            "fields": ("league", "season")
        }),
        ("Scoring Rules", {
            "fields": ("points_per_correct_pick", "key_pick_extra_points")
        }),
        ("Game Selection Rules", {
            "fields": ("spread_lock_weekday", "pickable_games_per_week", "picks_per_week")
        }),
        ("Key Pick Rules", {
            "fields": ("key_picks_enabled", "number_of_key_picks")
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "state", "zip", "country_code", "timezone", "latitude", "longitude", "elevation", "capacity", "year_constructed", "grass", "dome")
    list_filter = ("city", "state", "country_code", "timezone", "grass", "dome")
    search_fields = ("name", "city", "state", "zip", "country_code", "timezone")


@admin.register(LeagueGame)
class LeagueGameAdmin(admin.ModelAdmin):
    list_display = ("league", "game", "locked_spread_display", "spread_locked_at", "is_active", "selected_at")
    list_filter = ("league", "is_active", "spread_locked_at")
    search_fields = ("league__name", "game__home_team__name", "game__away_team__name")
    autocomplete_fields = ("league", "game")
    readonly_fields = ("selected_at", "spread_locked_at")
    actions = ["lock_spreads"]
    
    def locked_spread_display(self, obj):
        """Display locked spread in a readable format"""
        if obj.locked_home_spread is not None:
            return f"Home: {obj.locked_home_spread:+.1f} / Away: {obj.locked_away_spread:+.1f}"
        return "Not Locked"
    locked_spread_display.short_description = "Locked Spread"
    
    def lock_spreads(self, request, queryset):
        """Admin action to lock spreads for selected games"""
        locked_count = 0
        for league_game in queryset:
            if league_game.lock_spread():
                locked_count += 1
        self.message_user(request, f"Successfully locked spreads for {locked_count} game(s).")
    lock_spreads.short_description = "Lock current spreads for selected games"

@admin.register(Week)
class WeekAdmin(admin.ModelAdmin):
    list_display = ("season", "number", "season_type", "start_date", "end_date")
    list_filter = ("season", "season_type")
    search_fields = ("season__year", "number")
    autocomplete_fields = ("season",)
    readonly_fields = ("start_date", "end_date")

@admin.register(Ranking)
class RankingAdmin(admin.ModelAdmin):
    list_display = ("season", "week", "team", "poll", "rank", "first_place_votes", "points")
    list_filter = ("season", "week", "poll")
    search_fields = ("season__year", "week__number", "team__name", "poll")
    autocomplete_fields = ("season", "week", "team")

