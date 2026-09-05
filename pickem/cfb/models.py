import secrets
from datetime import timedelta

from django.db import models
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone


LEAGUE_INVITE_SALT = "cfb.league.invite"
LEAGUE_OPT_IN_SALT = "cfb.league.opt-in"
JOIN_PASSWORD_MIN_LENGTH = 4
PERSONAL_INVITE_EXPIRY_DAYS = 30
PERSONAL_INVITE_TOKEN_BYTES = 32


class League(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="created_leagues")
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    join_password = models.CharField(
        max_length=128,
        help_text="Hashed password required to join this league.",
    )
    invite_version = models.PositiveIntegerField(default=1)
    season_opt_in_required = models.BooleanField(
        default=False,
        help_text="Set when a new season starts. Owner must open the league for the year before members can opt in.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        # Ensure unique league name (case-insensitive)
        if League.objects.filter(name__iexact=self.name).exclude(pk=self.pk).exists():
            raise ValidationError({"name": "A league with this name already exists."})

    def set_join_password(self, raw_password: str) -> None:
        self.join_password = make_password(raw_password)

    def check_join_password(self, raw_password: str) -> bool:
        if not raw_password or not self.join_password:
            return False
        return check_password(raw_password, self.join_password)

    def get_invite_token(self) -> str:
        return signing.dumps(
            {"l": self.pk, "v": self.invite_version},
            salt=LEAGUE_INVITE_SALT,
        )

    def get_invite_path(self) -> str:
        return reverse("league_invite", kwargs={"token": self.get_invite_token()})

    def rotate_invite(self) -> None:
        self.invite_version += 1

    def save(self, *args, **kwargs):
        if not self.join_password:
            from django.utils.crypto import get_random_string
            self.set_join_password(get_random_string(20))
        super().save(*args, **kwargs)

    @classmethod
    def from_invite_token(cls, token: str):
        try:
            data = signing.loads(token, salt=LEAGUE_INVITE_SALT)
        except signing.BadSignature:
            return None
        league_id = data.get("l")
        version = data.get("v")
        if not isinstance(league_id, int) or not isinstance(version, int):
            return None
        return cls.objects.filter(
            pk=league_id,
            invite_version=version,
            is_active=True,
        ).first()


class LeagueInvite(models.Model):
    """Recipient-specific league invitation sent by email."""

    league = models.ForeignKey(
        League,
        on_delete=models.CASCADE,
        related_name="personal_invites",
    )
    email = models.EmailField()
    token = models.CharField(max_length=64, unique=True, db_index=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_league_invites",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["league", "email"]),
        ]
        verbose_name = "League Invite"
        verbose_name_plural = "League Invites"

    def __str__(self) -> str:
        return f"Invite {self.email} to {self.league.name}"

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_accepted(self) -> bool:
        return self.accepted_at is not None

    @property
    def is_pending(self) -> bool:
        return not self.is_revoked and not self.is_expired and not self.is_accepted

    def get_path(self) -> str:
        return reverse("personal_invite", kwargs={"token": self.token})

    def revoke(self) -> None:
        if not self.revoked_at:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at"])

    @classmethod
    def generate_token(cls) -> str:
        return secrets.token_urlsafe(PERSONAL_INVITE_TOKEN_BYTES)

    @classmethod
    def create_for_email(cls, league, email: str, invited_by=None, expires_in_days=None):
        """Create a new personal invite, revoking any pending invite for the same email."""
        normalized = email.strip().lower()
        expires_in_days = expires_in_days or PERSONAL_INVITE_EXPIRY_DAYS
        now = timezone.now()

        cls.objects.filter(
            league=league,
            email__iexact=normalized,
            accepted_at__isnull=True,
            revoked_at__isnull=True,
        ).update(revoked_at=now)

        return cls.objects.create(
            league=league,
            email=normalized,
            token=cls.generate_token(),
            invited_by=invited_by,
            expires_at=now + timedelta(days=expires_in_days),
        )


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
    
    TIEBREAKER_CHOICES = [
        (0, "None"),
        (1, "Correct Key Picks (if enabled)"),
        (2, "Total Points"),
        (3, "Correct Picks"),
    ]
    
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="rules")
    season = models.ForeignKey('Season', on_delete=models.CASCADE, related_name="league_rules")
    
    # Scoring Rules
    points_per_correct_pick = models.IntegerField(default=1, help_text="Points awarded for each correct pick")
    key_pick_extra_points = models.IntegerField(default=1, help_text="Extra points for correct key picks")
    drop_weeks = models.IntegerField(default=0, help_text="Number of weeks to drop from the season")
    
    # Game Mode
    against_the_spread_enabled = models.BooleanField(
        default=True,
        help_text="Allow users to pick against the spread"
    )
    
    force_hooks = models.BooleanField(
        default=False,
        help_text="Force users to pick with hooks"
    )
    
    tiebreaker = models.IntegerField(
        choices=TIEBREAKER_CHOICES,
        default=0,
        help_text="Tiebreaker rule"
    )
    
    # Game Selection Rules
    spread_lock_weekday = models.IntegerField(
        choices=WEEKDAY_CHOICES, 
        default=2,  # Wednesday
        help_text="Day of the week when spreads lock in place if against the spread is enabled"
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
    
    # Payout Structure
    entry_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        null=True,
        blank=True,
        help_text="Entry fee for the league"
    )
    weekly_payout_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        null=True,
        blank=True,
        help_text="Percentage of total pool allocated to weekly winners"
    )
    season_payout_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        null=True,
        blank=True,
        help_text="Percentage of total pool allocated to season winners"
    )
    weekly_payout_structure = models.JSONField(
        default=dict,
        blank=True,
        help_text="JSON structure defining percentage payout for each weekly position (e.g., {'1': 50, '2': 30, '3': 20})"
    )
    season_payout_structure = models.JSONField(
        default=dict,
        blank=True,
        help_text="JSON structure defining percentage payout for each season position (e.g., {'1': 60, '2': 30, '3': 10})"
    )
    season_payout_last_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        null=True,
        blank=True,
        help_text="Optional: Percentage of season payout allocated to last place"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("league", "season")
        ordering = ["-season__year"]
        verbose_name_plural = "League Rules"
        verbose_name = "League Rule"

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
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive members cannot make picks and are excluded from standings.",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("league", "user")
        ordering = ["-joined_at"]
        
        verbose_name = "League Membership"
        verbose_name_plural = "League Memberships"

    def __str__(self) -> str:
        status = "active" if self.is_active else "inactive"
        return f"{self.user.username} in {self.league.name} ({self.role}, {status})"

    def get_opt_in_token(self, season_year: int) -> str:
        return signing.dumps(
            {"l": self.league_id, "u": self.user_id, "y": season_year},
            salt=LEAGUE_OPT_IN_SALT,
        )

    @classmethod
    def from_opt_in_token(cls, token: str, season_year: int):
        try:
            data = signing.loads(token, salt=LEAGUE_OPT_IN_SALT)
        except signing.BadSignature:
            return None
        league_id = data.get("l")
        user_id = data.get("u")
        year = data.get("y")
        if not isinstance(league_id, int) or not isinstance(user_id, int) or year != season_year:
            return None
        return cls.objects.filter(league_id=league_id, user_id=user_id).select_related(
            "league", "user"
        ).first()


class Season(models.Model):
    year = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=False)
    
    # One-time data pull flags for CFBD API
    teams_pulled = models.BooleanField(default=False, help_text="Teams data pulled from CFBD for this season")
    games_pulled = models.BooleanField(default=False, help_text="Games data pulled from CFBD for this season")

    class Meta:
        ordering = ["-year"]

    def __str__(self) -> str:
        return self.name or str(self.year)


class Location(models.Model):
    """Geographic location data for venues and teams"""
    name = models.CharField(max_length=128, blank=True, null=True, help_text="Venue or location name")
    city = models.CharField(max_length=64, blank=True, null=True)
    state = models.CharField(max_length=64, blank=True, null=True)
    zip = models.CharField(max_length=16, blank=True, null=True)
    country_code = models.CharField(max_length=8, blank=True, null=True)
    timezone = models.CharField(max_length=64, blank=True, null=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    elevation = models.FloatField(null=True, blank=True, help_text="Elevation in feet")
    capacity = models.IntegerField(null=True, blank=True, help_text="Venue capacity")
    year_constructed = models.IntegerField(null=True, blank=True)
    grass = models.BooleanField(null=True, blank=True, help_text="True if grass, False if turf")
    dome = models.BooleanField(null=True, blank=True, help_text="True if dome/indoor")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name or f"{self.city}, {self.state}"


class Team(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="teams")
    # Store school name in `name` (e.g., "Michigan State")
    name = models.CharField(max_length=128)
    nickname = models.CharField(max_length=128, blank=True)  # mascot, e.g., "Spartans"
    abbreviation = models.CharField(max_length=16, blank=True)
    conference = models.CharField(max_length=64, blank=True, null=True)
    division = models.CharField(max_length=64, blank=True, null=True)  # Conference division (can be null)
    classification = models.CharField(max_length=16, blank=True)  # fbs, fcs, etc.
    logo_url = models.URLField(blank=True, null=True)
    
    # API IDs
    cfbd_id = models.IntegerField(null=True, blank=True, db_index=True)
    espn_id = models.CharField(max_length=32, null=True, blank=True, db_index=True)
    
    # Colors
    primary_color = models.CharField(max_length=7, blank=True, null=True)
    alt_color = models.CharField(max_length=7, blank=True, null=True)
    
    # Social & Web
    twitter = models.CharField(max_length=32, blank=True, null=True)
    
    # Location & Venue - use Location model
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name="teams")
    
    # Record tracking
    record_wins = models.PositiveIntegerField(default=0)
    record_losses = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("season", "name")
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.season.year})"


class Week(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="weeks")
    number = models.PositiveIntegerField()
    season_type = models.CharField(max_length=32, default="regular", help_text="regular, postseason, etc.")
    start_date = models.DateField()
    end_date = models.DateField()
    
    class Meta:
        ordering = ["season", "number"]
        unique_together = ("season", "number", "season_type")
        indexes = [
            models.Index(fields=["season", "number", "season_type"]),
        ]
        
    def __str__(self) -> str:
        return f"Week {self.number} - {self.season.year}"


class Game(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="games")
    external_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    week = models.ForeignKey(Week, on_delete=models.CASCADE, related_name="games", null=True, blank=True)
    season_type = models.CharField(max_length=32, blank=True, default="regular", help_text="regular, postseason, etc.")
    home_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="home_games")
    away_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="away_games")
    kickoff = models.DateTimeField()
    
    # Game metadata
    neutral_site = models.BooleanField(default=False)
    conference_game = models.BooleanField(default=False)
    attendance = models.IntegerField(null=True, blank=True)
    venue_name = models.CharField(max_length=128, blank=True)
    venue_id = models.IntegerField(null=True, blank=True)

    # Odds snapshots
    opening_home_spread = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    opening_away_spread = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    opening_over_under = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    current_home_spread = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    current_away_spread = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    current_over_under = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # Live status fields
    home_score = models.PositiveIntegerField(null=True, blank=True)
    away_score = models.PositiveIntegerField(null=True, blank=True)
    quarter = models.PositiveIntegerField(null=True, blank=True)
    clock = models.CharField(max_length=16, blank=True)
    # 'home', 'away', or blank when unknown / not in progress
    possession = models.CharField(max_length=4, blank=True, default="")
    is_final = models.BooleanField(default=False)

    class Meta:
        ordering = ["kickoff"]
        indexes = [
            models.Index(fields=["season", "week"]),
        ]

    def __str__(self) -> str:
        week_str = f"Week {self.week.number} - " if self.week else ""
        return f"{week_str}{self.away_team} at {self.home_team}"
    
    def has_started(self):
        """Check if the game has started (kickoff time has passed)"""
        from django.utils import timezone
        return timezone.now() >= self.kickoff


class GameSpread(models.Model):
    """Historical spread data for a game, allows tracking spread changes over time"""
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="spreads")
    week = models.ForeignKey(Week, on_delete=models.CASCADE, related_name="spreads", null=True, blank=True)
    home_spread = models.DecimalField(max_digits=5, decimal_places=2)
    away_spread = models.DecimalField(max_digits=5, decimal_places=2)
    timestamp = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=64, blank=True)  # e.g., bookmaker name

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["game", "-timestamp"]),
        ]
        verbose_name = "Game Spread"
        verbose_name_plural = "Game Spreads"

    def __str__(self) -> str:
        return f"{self.game} - {self.home_spread}/{self.away_spread} at {self.timestamp}"


class Ranking(models.Model):
    """Poll rankings for teams by week"""
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="rankings")
    week = models.ForeignKey(Week, on_delete=models.CASCADE, related_name="rankings", null=True, blank=True)
    season_type = models.CharField(max_length=32, default="regular", help_text="regular, postseason, etc.")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="rankings")
    poll = models.CharField(max_length=64, help_text="Poll name (e.g., AP Top 25, Coaches Poll)")
    rank = models.PositiveIntegerField()
    first_place_votes = models.PositiveIntegerField(default=0)
    points = models.PositiveIntegerField(default=0)
    
    class Meta:
        ordering = ["season", "week", "poll", "rank"]
        unique_together = ("season", "week", "season_type", "team", "poll")
        indexes = [
            models.Index(fields=["season", "week", "poll"]),
            models.Index(fields=["team", "season"]),
        ]
    
    def __str__(self) -> str:
        return f"{self.team.name} - #{self.rank} {self.poll} (Week {self.week.number}, {self.season.year})"


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
    
    is_total_points_game = models.BooleanField(default=False)
    
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
        verbose_name = "League Game"
        verbose_name_plural = "League Games"

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
    is_total_points_game = models.BooleanField(default=False)
    points_guess = models.IntegerField(null=True, blank=True)

    class Meta:
        unique_together = ("league", "game", "user")  # One pick per user per game per league
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["league", "user"]),
            models.Index(fields=["league", "game"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} -> {self.picked_team} in {self.league.name} ({self.game})"


class MemberWeek(models.Model):
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="member_weeks")
    week = models.ForeignKey(Week, on_delete=models.CASCADE, related_name="member_weeks")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="member_weeks")

    picks_made = models.PositiveIntegerField(default=0)
    correct = models.PositiveIntegerField(default=0)
    incorrect = models.PositiveIntegerField(default=0)
    ties = models.PositiveIntegerField(default=0)
    correct_key = models.PositiveIntegerField(default=0)
    points = models.IntegerField(default=0)

    # Optional total-points tiebreak cache (if you use it)
    points_guess = models.IntegerField(null=True, blank=True)
    points_actual = models.IntegerField(null=True, blank=True)
    tiebreak_abs_diff = models.IntegerField(null=True, blank=True)

    rank = models.PositiveIntegerField(null=True, blank=True)  # snapshot rank for the week
    scored_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("league", "week", "user")
        indexes = [models.Index(fields=["league", "week"]), models.Index(fields=["league", "points"])]
        

class MemberSeason(models.Model):
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="season_standings")
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="season_standings")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="season_standings")

    through_week = models.PositiveIntegerField(default=0)
    picks_made = models.PositiveIntegerField(default=0)
    correct = models.PositiveIntegerField(default=0)
    incorrect = models.PositiveIntegerField(default=0)
    ties = models.PositiveIntegerField(default=0)
    correct_key = models.PositiveIntegerField(default=0)
    points = models.IntegerField(default=0)
    points_dropped = models.IntegerField(default=0)
    
    picks_made_dropped = models.PositiveIntegerField(default=0)
    correct_dropped = models.PositiveIntegerField(default=0)
    incorrect_dropped = models.PositiveIntegerField(default=0)
    ties_dropped = models.PositiveIntegerField(default=0)
    correct_key_dropped = models.PositiveIntegerField(default=0)

    rank = models.PositiveIntegerField(null=True, blank=True)
    rank_with_drops = models.PositiveIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("league", "season", "user")
        indexes = [models.Index(fields=["league", "season"]), models.Index(fields=["league", "points"])]


class MemberSeasonPayment(models.Model):
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="season_payments")
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="member_payments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="season_payments")
    paid = models.BooleanField(
        default=False,
        help_text="Whether this member has paid the season entry fee.",
    )

    class Meta:
        unique_together = ("league", "season", "user")
        verbose_name = "Member Season Payment"
        verbose_name_plural = "Member Season Payments"

    def __str__(self) -> str:
        status = "paid" if self.paid else "unpaid"
        return f"{self.user.username} in {self.league.name} {self.season.year} ({status})"


class TeamStat(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="team_stats")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="team_stats")
    stat = models.CharField(max_length=128)
    value = models.FloatField()
    
    class Meta:
        unique_together = ("season", "team", "stat")
        indexes = [models.Index(fields=["season", "team"]), models.Index(fields=["season", "stat"])]
        
    def __str__(self) -> str:
        return f"{self.team.name} - {self.stat}: {self.value}"
