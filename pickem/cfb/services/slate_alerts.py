from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from ..models import League, LeagueRules, MemberSeason, MemberWeek, Pick, PushSubscription, Week, WeeklyPickPush
from .reminders import required_pick_count
from .weekly_push import slate_games


def missing_picks(device, league, ids, rules):
    picks = Pick.objects.filter(user_id=device.user_id, league=league, game_id__in=ids)
    required = required_pick_count(rules, len(ids)) if rules else len(ids)
    return picks.count() < required or (
        rules and rules.key_picks_enabled and picks.filter(is_key_pick=True).count() < rules.number_of_key_picks
    )


def event_still_wanted(push):
    if push.event not in ("remind_day", "remind_hours"):
        return True
    games = slate_games(push.league, push.week).filter(is_active=True).order_by("game__kickoff")
    first = games.values_list("game__kickoff", flat=True).first()
    rules = LeagueRules.objects.filter(league=push.league, season_id=push.week.season_id).first()
    return (getattr(push.subscription, push.event) and first == push.expires_at
            and missing_picks(push.subscription, push.league, list(games.values_list("game_id", flat=True)), rules))


def discover_events():
    if not (settings.WEB_PUSH_PRIVATE_KEY and settings.WEB_PUSH_PUBLIC_KEY):
        return
    now = timezone.now()
    weeks = Week.objects.filter(season__is_active=True, end_date__gte=(now - timedelta(days=1)).date(),
                                start_date__lte=(now + timedelta(days=1)).date())
    for league in League.objects.filter(is_active=True):
        devices = list(PushSubscription.objects.filter(user__is_active=True, user__league_memberships__league=league,
                                                       user__league_memberships__is_active=True))
        if devices:
            for week in weeks:
                queue_slate_events(league, week, devices, now)


def queue_slate_events(league, week, devices, now):
    games = [row.game for row in slate_games(league, week).filter(is_active=True)
             .select_related("game__home_team", "game__away_team").order_by("game__kickoff")]
    if not games:
        return
    kickoff = games[0].kickoff
    ids = [game.pk for game in games]
    rules = LeagueRules.objects.filter(league=league, season_id=week.season_id).first()
    existing = set(WeeklyPickPush.objects.filter(league=league, week=week).values_list("subscription_id", "event"))
    pending = []

    def queue(device, event, title, body, expires, page="picks", **extra):
        if (device.pk, event) in existing:
            return
        payload = dict(title=title, body=body, url=f"{reverse(page)}?league_id={league.pk}&week={week.pk}",
                       tag=f"{event}-{league.pk}-{week.pk}", **extra)
        pending.append(WeeklyPickPush(subscription=device, league=league, week=week, event=event,
                                      expires_at=expires, payload=payload))

    for device in devices:
        for game in games:
            if game.is_final and None not in (game.home_score, game.away_score) and game.kickoff >= now - timedelta(days=1):
                result = f"{game.away_team.abbreviation or game.away_team.name} {game.away_score} – {game.home_team.abbreviation or game.home_team.name} {game.home_score}"
                queue(device, f"final-{game.pk}", f"{league.name} · Week {week.number}", result,
                      now + timedelta(days=1), group=f"finals-{league.pk}-{week.pk}", game=game.pk)
        left = kickoff - now
        if timedelta(0) < left <= timedelta(hours=24):
            event = "remind_hours" if left <= timedelta(hours=3) else "remind_day"
            if getattr(device, event) and (device.pk, event) not in existing and missing_picks(device, league, ids, rules):
                queue(device, event, f"Finish your Week {week.number} picks", f"{league.name} · First kickoff {timezone.localtime(kickoff):%a %I:%M %p %Z}.", kickoff)
        if not all(game.is_final for game in games) or (device.pk, "recap") in existing:
            continue
        weekly = MemberWeek.objects.filter(user_id=device.user_id, league=league, week=week).first()
        season = MemberSeason.objects.filter(user_id=device.user_id, league=league, season_id=week.season_id).first()
        picks = Pick.objects.filter(user_id=device.user_id, league=league, game_id__in=ids).count()
        if picks and (not weekly or weekly.picks_made != picks or not season or season.updated_at < weekly.scored_at):
            continue
        record = f"{weekly.correct}–{weekly.incorrect}–{weekly.ties}, {weekly.points} points" if weekly else "No picks, 0 points"
        rank = (season.rank_with_drops if rules and rules.drop_weeks else season.rank) if season else None
        standing = f"Season rank #{rank}" if rank else "Unranked this season"
        queue(device, "recap", f"{league.name} · Week {week.number} complete", f"{record}. {standing}.",
              now + timedelta(days=1), "standings")
    WeeklyPickPush.objects.bulk_create(pending, ignore_conflicts=True)
