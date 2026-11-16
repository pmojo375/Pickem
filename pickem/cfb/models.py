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


DEFAULT_SPORT = "college-football"


class Sport(models.Model):
    """Configurable competition/feed namespace; add sports without schema changes."""

    slug = models.SlugField(primary_key=True, max_length=64)
    name = models.CharField(max_length=100)
    data_provider = models.CharField(max_length=32, blank=True, help_text="Registered adapter, e.g. cfbd or cbbd")
    espn_sport = models.SlugField(max_length=64, blank=True)
    espn_league = models.SlugField(max_length=64, blank=True)
    period_label = models.CharField(max_length=16, default="Period")
    regulation_periods = models.PositiveSmallIntegerField(default=4)
    has_possession = models.BooleanField(default=False)
    has_downs = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def scoreboard_urls(self):
        if not self.espn_sport or not self.espn_league:
            return ()
        path = f"{self.espn_sport}/{self.espn_league}/scoreboard"
        return tuple(
            f"https://{host}/apis/site/v2/sports/{path}"
            for host in ("site.web.api.espn.com", "site.api.espn.com")
        )

    def format_period(self, period):
        if not period:
            return ""
        if period > self.regulation_periods:
            overtime = period - self.regulation_periods
            return "OT" if overtime == 1 else f"{overtime}OT"
        separator = "" if self.period_label == "Q" else " "
        return f"{self.period_label}{separator}{period}"


def sport_field():
    return models.ForeignKey(
        Sport, on_delete=models.PROTECT, default=DEFAULT_SPORT,
    )


class IntegrityQuerySet(models.QuerySet):
    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        for obj in objs:
            obj.clean()
        return super().bulk_create(objs, **kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        objs = list(objs)
        for obj in objs:
            obj.clean()
        related = {f.name for f in self.model._meta.fields if f.is_relation}
        related |= {f.attname for f in self.model._meta.fields if f.is_relation}
        if related.intersection(fields):
            # Core bulk_update uses CASE expressions. Validate the objects above,
            # then write relationship batches without treating those CASEs as user input.
            from django.db import transaction
            with transaction.atomic(using=self.db):
                return sum(models.QuerySet.update(self.filter(pk=obj.pk), **{field: getattr(obj, field) for field in fields}) for obj in objs)
        return super().bulk_update(objs, fields, **kwargs)

    def update(self, **kwargs):
        # Validate relationship changes too; numerical scoring updates remain SQL-only.
        related = {f.name for f in self.model._meta.fields if f.is_relation}
        related |= {f.attname for f in self.model._meta.fields if f.is_relation}
        if related.intersection(kwargs):
            for obj in self:
                for name, value in kwargs.items():
                    if hasattr(value, "resolve_expression"):
                        raise ValidationError("Use validated model saves for relationship expressions.")
                    setattr(obj, name, value)
                obj.clean()
        return super().update(**kwargs)


class ValidatedModel(models.Model):
    objects = IntegrityQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class SportRelatedModel(ValidatedModel):
    """One canonical sport through a season, period, game, or league."""
    sport_source = "season"

    class Meta:
        abstract = True

    @property
    def sport(self):
        return getattr(self, self.sport_source).sport

    @property
    def sport_id(self):
        return getattr(self, self.sport_source).sport_id

    def clean(self):
        super().clean()
        season_id = getattr(self, "season_id", None)
        game = getattr(self, "game", None)
        schedule_period = getattr(self, "schedule_period", None)
        if season_id is None:
            season_id = game.season_id if game else (schedule_period.season_id if schedule_period else None)
        errors = {}
        if self.pk and getattr(self, "season_id", None) and type(self).objects.filter(pk=self.pk).exclude(season_id=self.season_id).exists():
            errors["season"] = "Create a separate record for a different season."
        for field in ("team", "home_team", "away_team", "schedule_period", "season_end_period"):
            obj = getattr(self, field, None)
            if obj and season_id and obj.season_id != season_id:
                errors[field] = "Must belong to the same season."
        league = getattr(self, "league", None)
        if league and season_id:
            season = Season.objects.only("sport_id").get(pk=season_id)
            if season.sport_id != league.sport_id:
                errors["league"] = "League and season must belong to the same sport."
        if game and hasattr(self, "picked_team_id") and self.picked_team_id not in (game.home_team_id, game.away_team_id):
            errors["picked_team"] = "Select a team playing in this game."
        if errors:
            raise ValidationError(errors)


class League(ValidatedModel):
    sport = sport_field()
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
        super().clean()
        if self.pk and League.objects.filter(pk=self.pk).exclude(sport_id=self.sport_id).exists():
            raise ValidationError({"sport": "Create a separate league to use a different sport."})
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


class LeagueRules(SportRelatedModel):
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
    drop_periods = models.IntegerField(default=0, help_text="Number of schedule_periods to drop from the season")
    
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
        help_text="Day of the schedule_period when spreads lock in place if against the spread is enabled"
    )
    pickable_games_per_period = models.IntegerField(
        default=10, 
        help_text="Maximum number of games available for picking each schedule_period"
    )
    picks_per_period = models.IntegerField(
        default=0,
        help_text="Number of picks required per schedule_period (0 = must pick all available games)"
    )
    
    # Key Pick Rules
    key_picks_enabled = models.BooleanField(
        default=True,
        help_text="Allow users to designate key picks for bonus points"
    )
    number_of_key_picks = models.IntegerField(
        default=1,
        help_text="Number of key picks allowed per schedule_period"
    )

    # Notifications
    pick_reminder_emails_enabled = models.BooleanField(
        default=False,
        help_text="Email members who have not finished their picks before the period's first game starts",
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
    period_payout_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        null=True,
        blank=True,
        help_text="Percentage of total pool allocated to period winners"
    )
    season_payout_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        null=True,
        blank=True,
        help_text="Percentage of total pool allocated to season winners"
    )
    period_payout_structure = models.JSONField(
        default=dict,
        blank=True,
        help_text="JSON structure defining percentage payout for each period position (e.g., {'1': 50, '2': 30, '3': 20})"
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
        help_text=(
            "Optional: Percentage of season payout for most incorrect picks "
            "(drop schedule_periods applied; missed games do not count as incorrect)"
        ),
    )
    season_end_period = models.ForeignKey(
        'SchedulePeriod',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text="When this schedule_period's slate is final (or its end date has passed), the league season is final and prize money can show on standings",
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("league", "season")
        ordering = ["-season__year"]
        verbose_name_plural = "League Rules"
        verbose_name = "League Rule"

    def clean(self):
        super().clean()
        if self.league_id and self.season_id and self.league.sport_id != self.season.sport_id:
            raise ValidationError({"season": "Season and league must belong to the same sport."})
        if self.season_end_period_id and self.season_end_period.season_id != self.season_id:
            raise ValidationError({"season_end_period": "Must belong to the selected season."})

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


class Season(ValidatedModel):
    sport = sport_field()
    year = models.PositiveIntegerField()
    name = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=False)
    
    # Provider import state for this season
    teams_pulled = models.BooleanField(default=False, help_text="Teams imported for this season")
    games_pulled = models.BooleanField(default=False, help_text="Games imported for this season")

    class Meta:
        ordering = ["-year", "sport_id"]
        constraints = [
            models.UniqueConstraint(fields=["sport", "year"], name="season_sport_year_unique"),
            models.UniqueConstraint(fields=["sport"], condition=models.Q(is_active=True), name="one_active_season_per_sport"),
        ]

    def clean(self):
        super().clean()
        if self.pk and Season.objects.filter(pk=self.pk).exclude(sport_id=self.sport_id).exists():
            raise ValidationError({"sport": "Create a separate season to use a different sport."})

    def __str__(self) -> str:
        return self.name or f"{self.sport.name} {self.year}"


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


class Team(SportRelatedModel):
    def clean(self):
        super().clean()
        if self.pk and Team.objects.filter(pk=self.pk).exclude(season_id=self.season_id).exists():
            raise ValidationError({"season": "Create a separate team record for another season."})

    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="teams")
    # Store school name in `name` (e.g., "Michigan State")
    name = models.CharField(max_length=128)
    nickname = models.CharField(max_length=128, blank=True)  # mascot, e.g., "Spartans"
    abbreviation = models.CharField(max_length=16, blank=True)
    conference = models.CharField(max_length=64, blank=True, null=True)
    division = models.CharField(max_length=64, blank=True, null=True)  # Conference division (can be null)
    classification = models.CharField(max_length=16, blank=True)  # fbs, fcs, etc.
    logo_url = models.URLField(blank=True, null=True)
    
    provider_ids = models.JSONField(default=dict, blank=True, help_text="External IDs keyed by provider, e.g. {\"espn\": \"150\"}")

    # Legacy CFBD and ESPN identifiers retained for existing integrations
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
    ats_wins = models.PositiveIntegerField(default=0)
    ats_losses = models.PositiveIntegerField(default=0)
    ats_pushes = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("season", "name")
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.season.year})"


class SchedulePeriod(SportRelatedModel):
    class Kind(models.TextChoices):
        WEEK = "week", "Week"
        ROUND = "round", "Round"
        MATCHDAY = "matchday", "Matchday"
        DAY = "day", "Day"
        SLATE = "slate", "Slate"

    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.WEEK)

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End must be on or after start."})

    name = models.CharField(max_length=64, blank=True, help_text="Optional round or slate name, e.g. Sweet 16")

    @property
    def display_name(self):
        return self.name or f"{self.get_kind_display()} {self.number}"

    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="schedule_periods")
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
        return f"{self.display_name} - {self.season.year}"


class Game(SportRelatedModel):
    provider_ids = models.JSONField(default=dict, blank=True, help_text="External IDs keyed by provider")

    @property
    def period_display(self):
        return self.season.sport.format_period(self.period)

    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="games")
    tournament = models.CharField(max_length=64, blank=True)
    home_seed = models.PositiveSmallIntegerField(null=True, blank=True)
    away_seed = models.PositiveSmallIntegerField(null=True, blank=True)
    external_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    schedule_period = models.ForeignKey(SchedulePeriod, on_delete=models.CASCADE, related_name="games", null=True, blank=True)
    season_type = models.CharField(max_length=32, blank=True, default="regular", help_text="regular, postseason, etc.")
    home_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="home_games")
    away_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="away_games")
    start_time = models.DateTimeField()
    
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
    period = models.PositiveIntegerField(null=True, blank=True)
    clock = models.CharField(max_length=16, blank=True)
    # 'home', 'away', or blank when unknown / not in progress
    possession = models.CharField(max_length=4, blank=True, default="")
    # ESPN situation: e.g. "3rd & 5" / ball spot e.g. "MSST 30"
    down_distance_text = models.CharField(max_length=32, blank=True, default="")
    ball_on = models.CharField(max_length=32, blank=True, default="")
    is_final = models.BooleanField(default=False)

    class Meta:
        ordering = ["start_time"]
        indexes = [
            models.Index(fields=["season", "schedule_period"]),
        ]

    def __str__(self) -> str:
        period_str = f"{self.schedule_period.display_name} - " if self.schedule_period else ""
        return f"{period_str}{self.away_team} at {self.home_team}"
    
    def clean(self):
        super().clean()
        errors = {}
        for field in ("home_team", "away_team", "schedule_period"):
            obj = getattr(self, field, None)
            if obj and obj.season_id != self.season_id:
                errors[field] = "Must belong to the game's season."
        if self.home_team_id and self.home_team_id == self.away_team_id:
            errors["away_team"] = "Teams must be different."
        if errors:
            raise ValidationError(errors)

    def has_started(self):
        """Check if the game has started (start_time time has passed)"""
        from django.utils import timezone
        return timezone.now() >= self.start_time


class GameSpread(SportRelatedModel):
    sport_source = "game"
    """Historical spread data for a game, allows tracking spread changes over time"""
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="spreads")
    schedule_period = models.ForeignKey(SchedulePeriod, on_delete=models.CASCADE, related_name="spreads", null=True, blank=True)
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


class Ranking(SportRelatedModel):
    """Poll rankings for teams by schedule_period"""
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="rankings")
    schedule_period = models.ForeignKey(SchedulePeriod, on_delete=models.CASCADE, related_name="rankings", null=True, blank=True)
    season_type = models.CharField(max_length=32, default="regular", help_text="regular, postseason, etc.")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="rankings")
    poll = models.CharField(max_length=64, help_text="Poll name (e.g., AP Top 25, Coaches Poll)")
    rank = models.PositiveIntegerField()
    first_place_votes = models.PositiveIntegerField(default=0)
    points = models.PositiveIntegerField(default=0)
    
    class Meta:
        ordering = ["season", "schedule_period", "poll", "rank"]
        unique_together = ("season", "schedule_period", "season_type", "team", "poll")
        indexes = [
            models.Index(fields=["season", "schedule_period", "poll"]),
            models.Index(fields=["team", "season"]),
        ]
    
    def __str__(self) -> str:
        return f"{self.team.name} - #{self.rank} {self.poll} ({self.schedule_period or self.season})"


class LeagueGame(SportRelatedModel):
    sport_source = "game"
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
        ordering = ["game__start_time"]
        indexes = [
            models.Index(fields=["league", "game"]),
        ]
        verbose_name = "League Game"
        verbose_name_plural = "League Games"

    def __str__(self) -> str:
        return f"{self.league.name}: {self.game}"
    
    def clean(self):
        super().clean()
        if self.league_id and self.game_id and self.league.sport_id != self.game.season.sport_id:
            raise ValidationError({"game": "Game and league must belong to the same sport."})

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


class Pick(SportRelatedModel):
    sport_source = "game"
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


class MemberPeriod(SportRelatedModel):
    sport_source = "schedule_period"
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="member_periods")
    schedule_period = models.ForeignKey(SchedulePeriod, on_delete=models.CASCADE, related_name="member_periods")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="member_periods")

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

    rank = models.PositiveIntegerField(null=True, blank=True)  # snapshot rank for the schedule_period
    scored_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("league", "schedule_period", "user")
        indexes = [models.Index(fields=["league", "schedule_period"]), models.Index(fields=["league", "points"])]
        

class MemberSeason(SportRelatedModel):
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="season_standings")
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="season_standings")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="season_standings")

    through_period = models.PositiveIntegerField(default=0)
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


class MemberSeasonPayment(SportRelatedModel):
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="season_payments")
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="member_payments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="season_payments")
    paid = models.BooleanField(
        default=False,
        help_text="Whether this member has paid the season entry fee.",
    )
    paid_receipt_dismissed = models.BooleanField(
        default=False,
        help_text="Whether the user dismissed the 'entry fee received' home alert for this season.",
    )

    class Meta:
        unique_together = ("league", "season", "user")
        verbose_name = "Member Season Payment"
        verbose_name_plural = "Member Season Payments"

    def __str__(self) -> str:
        status = "paid" if self.paid else "unpaid"
        return f"{self.user.username} in {self.league.name} {self.season.year} ({status})"


class TeamStat(SportRelatedModel):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="team_stats")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="team_stats")
    stat = models.CharField(max_length=128)
    value = models.FloatField()
    
    class Meta:
        unique_together = ("season", "team", "stat")
        indexes = [models.Index(fields=["season", "team"]), models.Index(fields=["season", "stat"])]
        
    def __str__(self) -> str:
        return f"{self.team.name} - {self.stat}: {self.value}"


class LeagueAnnouncement(models.Model):
    """League-manager message shown to active members (site-wide can come later)."""

    KIND_ONE_TIME = "one_time"
    KIND_PERSISTENT = "persistent"
    KIND_CHOICES = [
        (KIND_ONE_TIME, "One-time"),
        (KIND_PERSISTENT, "Persistent"),
    ]
    LEVEL_INFO = "info"
    LEVEL_SUCCESS = "success"
    LEVEL_WARNING = "warning"
    LEVEL_ERROR = "error"
    LEVEL_CHOICES = [
        (LEVEL_INFO, "Info"),
        (LEVEL_SUCCESS, "Success"),
        (LEVEL_WARNING, "Warning"),
        (LEVEL_ERROR, "Error"),
    ]

    league = models.ForeignKey(
        League,
        on_delete=models.CASCADE,
        related_name="announcements",
    )
    title = models.CharField(max_length=200)
    body = models.TextField()
    level = models.CharField(max_length=16, choices=LEVEL_CHOICES, default=LEVEL_INFO)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, default=KIND_ONE_TIME)
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Optional. If set, hidden until this time.",
    )
    ends_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Optional. If set, hidden after this time.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_league_announcements",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["league", "is_active"]),
        ]
        verbose_name = "League Announcement"
        verbose_name_plural = "League Announcements"

    def __str__(self) -> str:
        return f"{self.league.name}: {self.title}"

    def is_visible_now(self, now=None) -> bool:
        if not self.is_active:
            return False
        now = now or timezone.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now >= self.ends_at:
            return False
        return True


class UserAnnouncementDismissal(models.Model):
    """Per-user dismiss record for one-time league announcements."""

    announcement = models.ForeignKey(
        LeagueAnnouncement,
        on_delete=models.CASCADE,
        related_name="dismissals",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="announcement_dismissals",
    )
    dismissed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("announcement", "user")
        ordering = ["-dismissed_at"]
        verbose_name = "Announcement Dismissal"
        verbose_name_plural = "Announcement Dismissals"

    def __str__(self) -> str:
        return f"{self.user.username} dismissed {self.announcement_id}"


class UserProfile(models.Model):
    """Per-user settings that don't belong on Django's auth User."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    secondary_email = models.EmailField(
        blank=True,
        default="",
        help_text="Optional. Reminders, invites, and opt-in emails are also sent here.",
    )

    class Meta:
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"

    def __str__(self) -> str:
        return f"Profile for {self.user.username}"


def user_notification_emails(user) -> list[str]:
    """Primary account email plus optional secondary, de-duplicated."""
    emails: list[str] = []
    seen: set[str] = set()

    def add(raw: str | None) -> None:
        email = (raw or "").strip()
        if not email:
            return
        key = email.lower()
        if key in seen:
            return
        seen.add(key)
        emails.append(email)

    add(getattr(user, "email", None))
    try:
        add(user.profile.secondary_email)
    except UserProfile.DoesNotExist:
        pass
    return emails
