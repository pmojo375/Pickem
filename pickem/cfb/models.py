from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError


class League(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="created_leagues")
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        # Ensure unique league name (case-insensitive)
        if League.objects.filter(name__iexact=self.name).exclude(pk=self.pk).exists():
            raise ValidationError({"name": "A league with this name already exists."})


class LeagueRules(models.Model):
    """
    Season-specific rules for a league.
    Each league can have different rules for different seasons.
    """
    WEEKDAY_CHOICES = [
        (0, "Monday"),
        (1, "Tuesday"),
        (2, "Wednesday"),
        (3, "Thursday"),
        (4, "Friday"),
        (5, "Saturday"),
        (6, "Sunday"),
    ]
    
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="rules")
    season = models.ForeignKey('Season', on_delete=models.CASCADE, related_name="league_rules")
    
    # Scoring Rules
    points_per_correct_pick = models.IntegerField(default=1, help_text="Points awarded for each correct pick")
    key_pick_extra_points = models.IntegerField(default=1, help_text="Extra points for correct key picks")
    
    # Game Selection Rules
    spread_lock_weekday = models.IntegerField(
        choices=WEEKDAY_CHOICES, 
        default=2,  # Wednesday
        help_text="Day of the week when spreads lock in place"
    )
    pickable_games_per_week = models.IntegerField(
        default=10, 
        help_text="Maximum number of games available for picking each week"
    )
    picks_per_week = models.IntegerField(
        default=0,
        help_text="Number of picks required per week (0 = must pick all available games)"
    )
    
    # Key Pick Rules
    key_picks_enabled = models.BooleanField(
        default=True,
        help_text="Allow users to designate key picks for bonus points"
    )
    number_of_key_picks = models.IntegerField(
        default=1,
        help_text="Number of key picks allowed per week"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("league", "season")
        ordering = ["-season__year"]
        verbose_name_plural = "League rules"

    def __str__(self) -> str:
        return f"{self.league.name} - {self.season.year} Rules"


class LeagueMembership(models.Model):
    ROLE_CHOICES = [
        ("owner", "Owner"),
        ("admin", "Admin"),
        ("member", "Member"),
    ]
    
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="league_memberships")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="member")
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("league", "user")
        ordering = ["-joined_at"]

    def __str__(self) -> str:
        return f"{self.user.username} in {self.league.name} ({self.role})"


class Season(models.Model):
    year = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=False)

    class Meta:
        ordering = ["-year"]

    def __str__(self) -> str:
        return self.name or str(self.year)


class Team(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="teams")
    # Store school name in `name` (e.g., "Michigan State")
    name = models.CharField(max_length=128)
    nickname = models.CharField(max_length=128, blank=True)  # mascot, e.g., "Spartans"
    abbreviation = models.CharField(max_length=16, blank=True)
    conference = models.CharField(max_length=64, blank=True)
    logo_url = models.URLField(blank=True)
    cfbd_id = models.IntegerField(null=True, blank=True, db_index=True)
    espn_id = models.CharField(max_length=32, null=True, blank=True, db_index=True)
    primary_color = models.CharField(max_length=7, blank=True)
    alt_color = models.CharField(max_length=7, blank=True)
    twitter = models.CharField(max_length=32, blank=True)
    city = models.CharField(max_length=64, blank=True)
    state = models.CharField(max_length=64, blank=True)
    venue_name = models.CharField(max_length=128, blank=True)
    venue_id = models.IntegerField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    record_wins = models.PositiveIntegerField(default=0)
    record_losses = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("season", "name")
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.season.year})"


class Rules(models.Model):
    season = models.OneToOneField(Season, on_delete=models.CASCADE, related_name="rules")
    max_key_picks_per_week = models.PositiveIntegerField(default=1)
    points_per_correct_pick = models.IntegerField(default=1)
    points_per_key_pick = models.IntegerField(default=2)

    def __str__(self) -> str:
        return f"Rules {self.season.year}"


class Game(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="games")
    external_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    home_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="home_games")
    away_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="away_games")
    kickoff = models.DateTimeField()

    # Odds snapshots
    opening_home_spread = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    opening_away_spread = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    opening_total = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    current_home_spread = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    current_away_spread = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    current_total = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # Live status fields
    home_score = models.PositiveIntegerField(null=True, blank=True)
    away_score = models.PositiveIntegerField(null=True, blank=True)
    quarter = models.PositiveIntegerField(null=True, blank=True)
    clock = models.CharField(max_length=16, blank=True)
    is_final = models.BooleanField(default=False)

    class Meta:
        ordering = ["kickoff"]

    def __str__(self) -> str:
        return f"{self.away_team} at {self.home_team}"
    
    def has_started(self):
        """Check if the game has started (kickoff time has passed)"""
        from django.utils import timezone
        return timezone.now() >= self.kickoff


class GameSpread(models.Model):
    """Historical spread data for a game, allows tracking spread changes over time"""
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="spreads")
    home_spread = models.DecimalField(max_digits=5, decimal_places=2)
    away_spread = models.DecimalField(max_digits=5, decimal_places=2)
    timestamp = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=64, blank=True)  # e.g., bookmaker name

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["game", "-timestamp"]),
        ]

    def __str__(self) -> str:
        return f"{self.game} - {self.home_spread}/{self.away_spread} at {self.timestamp}"


class LeagueGame(models.Model):
    """
    Represents a game selected for a specific league's pick'em.
    Each league can select different games and lock spreads at different times.
    """
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="league_games")
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="league_selections")
    
    # League-specific locked spread (frozen when this league selects the game)
    locked_home_spread = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    locked_away_spread = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    spread_locked_at = models.DateTimeField(null=True, blank=True)
    
    # When this game was added to the league
    selected_at = models.DateTimeField(auto_now_add=True)
    
    # League-specific settings for this game
    is_active = models.BooleanField(default=True)  # Can be disabled without deleting
    
    class Meta:
        unique_together = ("league", "game")
        ordering = ["game__kickoff"]
        indexes = [
            models.Index(fields=["league", "game"]),
        ]

    def __str__(self) -> str:
        return f"{self.league.name}: {self.game}"
    
    def lock_spread(self):
        """Lock the current spread for this league's game"""
        from django.utils import timezone
        if self.game.current_home_spread is not None:
            self.locked_home_spread = self.game.current_home_spread
            self.locked_away_spread = self.game.current_away_spread
            self.spread_locked_at = timezone.now()
            self.save(update_fields=["locked_home_spread", "locked_away_spread", "spread_locked_at"])
            return True
        return False


class Pick(models.Model):
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="picks")
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="picks")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="picks")
    picked_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="picks")
    is_key_pick = models.BooleanField(default=False)
    is_correct = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("league", "game", "user")  # One pick per user per game per league
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["league", "user"]),
            models.Index(fields=["league", "game"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} -> {self.picked_team} in {self.league.name} ({self.game})"

