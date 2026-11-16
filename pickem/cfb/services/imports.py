"""Idempotent basketball ingestion into shared models, with bounded API requests."""
from datetime import datetime, time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from cfb.models import Game, GameSpread, SchedulePeriod, Season, Team
from .providers import get_provider, ProviderError


def _id(value):
    if value is None or value == "":
        raise ProviderError("Provider record is missing its stable identifier.")
    return str(value)


def _team(season, provider_id, name, data=None):
    data = data or {}
    provider_id = _id(provider_id)
    team = Team.objects.filter(season=season, provider_ids__cbbd=provider_id).first()
    if team is None:
        # Reconcile team placeholders created by a games-only sync by stable ID/name.
        team = Team.objects.filter(season=season, name=name).first() or Team(season=season, name=name)
        existing_id = team.provider_ids.get("cbbd")
        if existing_id and existing_id != provider_id:
            raise ProviderError(f"Conflicting CBBD team IDs for {name}.")
    ids = dict(team.provider_ids)
    ids["cbbd"] = provider_id
    if data.get("sourceId"):
        ids["espn"] = str(data["sourceId"])
        team.espn_id = ids["espn"]
    team.provider_ids = ids
    team.name = name
    for remote, local in (("mascot", "nickname"), ("abbreviation", "abbreviation"), ("conference", "conference"), ("primaryColor", "primary_color"), ("secondaryColor", "alt_color")):
        if data.get(remote) is not None:
            setattr(team, local, data[remote])
    if data.get("logos"):
        team.logo_url = data["logos"][0]
    team.save()
    return team


def _game(season, row):
    if int(row.get("season", season.year)) != season.year:
        raise ProviderError("Game season differs from the requested season.")
    start = parse_datetime(row.get("startDate", ""))
    if start is None:
        raise ProviderError("Game has no valid startDate.")
    if timezone.is_naive(start):
        start = timezone.make_aware(start)
    provider_id = _id(row.get("id"))
    game = Game.objects.filter(season=season, provider_ids__cbbd=provider_id).first()
    espn_id = str(row.get("sourceId") or "")
    if game is None and espn_id:
        game = Game.objects.filter(season=season, external_id=espn_id).first()
    game = game or Game(season=season)
    game.provider_ids = {**game.provider_ids, "cbbd": provider_id}
    if espn_id:
        game.provider_ids["espn"] = espn_id
        game.external_id = espn_id
    game.home_team = _team(season, row.get("homeTeamId"), row["homeTeam"])
    game.away_team = _team(season, row.get("awayTeamId"), row["awayTeam"])
    game.start_time = start
    game.season_type = row.get("seasonType") or "regular"
    # Respect explicitly configured rounds/slates. Otherwise create a daily slate.
    day = timezone.localtime(start).date()
    existing_period = game.schedule_period if game.schedule_period_id else None
    if not existing_period or existing_period.kind == SchedulePeriod.Kind.DAY:
        matching = SchedulePeriod.objects.filter(season=season, season_type=game.season_type, start_date__lte=day, end_date__gte=day).exclude(kind=SchedulePeriod.Kind.DAY).order_by("start_date", "number").first()
        if not matching:
            matching, _ = SchedulePeriod.objects.get_or_create(
                season=season, season_type=game.season_type, number=day.toordinal(),
                defaults={"name": day.isoformat(), "kind": SchedulePeriod.Kind.DAY, "start_date": day, "end_date": day},
            )
        game.schedule_period = matching
    game.tournament = row.get("tournament") or ""
    game.home_seed = row.get("homeSeed")
    game.away_seed = row.get("awaySeed")
    for remote, local in (("neutralSite", "neutral_site"), ("conferenceGame", "conference_game"), ("attendance", "attendance"), ("venue", "venue_name"), ("venueId", "venue_id")):
        if row.get(remote) is not None:
            setattr(game, local, row[remote])
    # CBBD owns schedule/final results. Never overwrite a newer ESPN in-play score.
    completed = str(row.get("status", "")).lower() == "final"
    if completed:
        if row.get("homePoints") is None or row.get("awayPoints") is None:
            raise ProviderError("Final game is missing its scores.")
        game.home_score, game.away_score = row["homePoints"], row["awayPoints"]
        game.is_final = True
    game.save()
    return game


def _lines(season, rows):
    updated = 0
    for row in rows:
        game = Game.objects.filter(season=season, provider_ids__cbbd=str(row.get("gameId"))).first()
        if game is None:
            continue
        lines = [line for line in row.get("lines", []) if line.get("spread") is not None]
        if not lines:
            continue
        line = next((line for line in lines if str(line.get("provider", "")).lower() == "consensus"), sorted(lines, key=lambda line: str(line.get("provider", "")))[0])
        home = Decimal(str(line["spread"]))
        total = Decimal(str(line["overUnder"])) if line.get("overUnder") is not None else None
        source = str(line.get("provider") or "CBBD")
        previous = game.spreads.filter(source=source).first()
        if previous is None or previous.home_spread != home:
            GameSpread.objects.create(game=game, schedule_period=game.schedule_period, home_spread=home, away_spread=-home, source=source)
        if game.opening_home_spread is None:
            opening = Decimal(str(line.get("spreadOpen"))) if line.get("spreadOpen") is not None else home
            game.opening_home_spread, game.opening_away_spread = opening, -opening
            game.opening_over_under = Decimal(str(line["overUnderOpen"])) if line.get("overUnderOpen") is not None else total
        game.current_home_spread, game.current_away_spread, game.current_over_under = home, -home, total
        game.save()
        updated += 1
    return updated


def sync_sport_data(season, *, start=None, end=None, season_type=None, tournament=None, teams_only=False, games_only=False, include_lines=False, max_requests=3, force=False):
    if season.sport.data_provider != "cbbd":
        raise ProviderError("This importer requires a registered CBBD basketball sport; use the football initializer for CFBD.")
    if teams_only and games_only:
        raise ProviderError("Choose either teams-only or games-only.")
    if not teams_only and (start is None or end is None or start > end):
        raise ProviderError("Provide an ordered start/end date range for a bounded game import.")
    if max_requests < 1 or max_requests > 20:
        raise ProviderError("Request budget must be between 1 and 20.")
    client = get_provider(season.sport, max_requests=max_requests, force=force)
    start_str = timezone.make_aware(datetime.combine(start, time.min)).isoformat() if start else None
    end_str = timezone.make_aware(datetime.combine(end, time.max)).isoformat() if end else None
    # Fetch every requested payload before making database changes.
    teams = None if games_only else client.teams(season.year)
    games = None if teams_only else client.games(season.year, start_str, end_str, season_type, tournament)
    lines = client.lines(season.year, start_str, end_str) if include_lines and not teams_only else []
    with transaction.atomic():
        season = Season.objects.select_for_update().get(pk=season.pk)
        if teams is not None:
            for row in teams:
                _team(season, row.get("id"), row["school"], row)
            season.teams_pulled = True
        if games is not None:
            for row in games:
                if tournament and str(row.get("tournament", "")).casefold() != tournament.casefold():
                    continue
                _game(season, row)
            # A bounded tournament window is not an import of the entire season.
        line_count = _lines(season, lines)
        season.save(update_fields=["teams_pulled"])
    if games:
        from .records import update_team_records
        update_team_records(season.year, sport_id=season.sport_id)
    return {"teams": len(teams or []), "games": len(games or []), "lines": line_count, "requests": client.requests_used}
