from django.contrib import admin
from .models import Season, Team, Game, GameSpread, Rules, Pick, League, LeagueMembership, LeagueGame, LeagueRules


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ("year", "name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("year", "name")


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "abbreviation", "nickname", "conference", "season", "cfbd_id", "espn_id", "primary_color", "alt_color", "twitter")
    list_filter = ("season", "conference")
    search_fields = ("name", "nickname", "abbreviation", "cfbd_id", "espn_id", "twitter")


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = (
        "season",
        "kickoff",
        "away_team",
        "home_team",
        "current_spread_display",
        "away_score",
        "home_score",
        "quarter",
        "clock",
        "is_final",
    )
    list_filter = ("season", "is_final")
    search_fields = ("home_team__name", "away_team__name")
    autocomplete_fields = ("home_team", "away_team")
    readonly_fields = ("opening_spread_display", "current_spread_display")
    
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


@admin.register(Rules)
class RulesAdmin(admin.ModelAdmin):
    list_display = (
        "season",
        "max_key_picks_per_week",
        "points_per_correct_pick",
        "points_per_key_pick",
    )


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

