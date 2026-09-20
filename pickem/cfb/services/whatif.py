"""
Ephemeral what-if standings simulation.

Users assign outcomes to started, unfinished slate games and see hypothetical
week/season ranks. Never writes Pick, MemberWeek, or MemberSeason.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Tuple

from django.contrib.auth import get_user_model
from django.utils import timezone

from ..models import (
    Game,
    League,
    LeagueGame,
    LeagueMembership,
    LeagueRules,
    MemberSeason,
    MemberWeek,
    Pick,
    Week,
)
from .scoring import (
    _tiebreak_cascade,
    calculate_pick_points,
    filter_active_league_picks,
    is_pick_correct,
)

logger = logging.getLogger(__name__)

User = get_user_model()

VALID_OUTCOMES = frozenset({"home", "away", "push"})


class WhatIfError(ValueError):
    """Raised when what-if inputs are invalid."""


@dataclass
class WeekStat:
    user_id: int
    picks_made: int = 0
    correct: int = 0
    incorrect: int = 0
    ties: int = 0
    correct_key: int = 0
    points: int = 0
    points_guess: Optional[int] = None
    points_actual: Optional[int] = None
    tiebreak_abs_diff: Optional[int] = None
    rank: int = 0


@dataclass
class SeasonStat:
    user_id: int
    picks_made: int = 0
    correct: int = 0
    incorrect: int = 0
    ties: int = 0
    correct_key: int = 0
    points: int = 0
    points_dropped: int = 0
    picks_made_dropped: int = 0
    correct_dropped: int = 0
    incorrect_dropped: int = 0
    ties_dropped: int = 0
    correct_key_dropped: int = 0
    rank: int = 0
    rank_with_drops: int = 0


@dataclass
class WhatIfResult:
    games: List[dict] = field(default_factory=list)
    week_standings: List[dict] = field(default_factory=list)
    season_standings: List[dict] = field(default_factory=list)
    allows_push: bool = False
    against_the_spread: bool = True
    errors: List[str] = field(default_factory=list)


def synthesize_scores(
    outcome: str,
    *,
    against_the_spread: bool,
    locked_home_spread: Optional[Decimal],
    force_hooks: bool,
) -> Tuple[int, int]:
    """
    Build minimal integer scores that produce the requested outcome.

    Returns (home_score, away_score).
    """
    if outcome not in VALID_OUTCOMES:
        raise WhatIfError(f"Invalid outcome '{outcome}'")

    if not against_the_spread:
        if outcome == "push":
            raise WhatIfError("Push is not valid for straight-up scoring")
        if outcome == "home":
            return 1, 0
        return 0, 1

    if locked_home_spread is None:
        raise WhatIfError("Cannot simulate ATS outcome without a locked spread")

    try:
        spread = Decimal(str(locked_home_spread))
    except (TypeError, InvalidOperation) as exc:
        raise WhatIfError("Invalid locked spread") from exc

    if force_hooks:
        from .hooks import apply_forced_hook

        spread = apply_forced_hook(spread)

    threshold = -spread  # home covers when margin > threshold

    if outcome == "push":
        if force_hooks:
            raise WhatIfError("Push is not available when force hooks are enabled")
        if threshold != threshold.to_integral_value():
            raise WhatIfError("Push is not possible with a half-point spread")
        margin = int(threshold)
    elif outcome == "home":
        # Smallest integer margin strictly greater than threshold.
        margin = int(threshold.to_integral_value(rounding=ROUND_FLOOR)) + 1
    else:  # away
        # Largest integer margin strictly less than threshold.
        margin = int(threshold.to_integral_value(rounding=ROUND_CEILING)) - 1

    # Keep scores non-negative with a comfortable buffer.
    base = 28
    if margin >= 0:
        return base + margin, base
    return base, base - margin


def _game_as_final(game: Game, home_score: int, away_score: int) -> Game:
    """Shallow copy of game with final scores set (unsaved)."""
    simulated = copy.copy(game)
    simulated.is_final = True
    simulated.home_score = home_score
    simulated.away_score = away_score
    return simulated


def _classify_league_games(
    league_games: Iterable[LeagueGame],
) -> Tuple[List[LeagueGame], List[LeagueGame], List[LeagueGame]]:
    """Split into (final, started_non_final, unstarted)."""
    now = timezone.now()
    final_games: List[LeagueGame] = []
    started: List[LeagueGame] = []
    unstarted: List[LeagueGame] = []

    for lg in league_games:
        game = lg.game
        if game.is_final:
            final_games.append(lg)
        elif game.kickoff <= now:
            started.append(lg)
        else:
            unstarted.append(lg)

    return final_games, started, unstarted


def _serialize_simulatable_game(
    lg: LeagueGame,
    *,
    against_the_spread: bool,
    allows_push: bool,
    for_json: bool = False,
) -> dict:
    game = lg.game
    away = game.away_team
    home = game.home_team
    data = {
        "id": game.id,
        "away_team_id": game.away_team_id,
        "home_team_id": game.home_team_id,
        "locked_home_spread": (
            str(lg.locked_home_spread) if lg.locked_home_spread is not None else None
        ),
        "is_live": game.home_score is not None or game.away_score is not None,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "against_the_spread": against_the_spread,
        "allows_push": allows_push,
    }
    if for_json:
        data["away_team"] = away.name
        data["home_team"] = home.name
        data["away_abbreviation"] = away.abbreviation or away.name
        data["home_abbreviation"] = home.abbreviation or home.name
        data["away_logo"] = f"logos/{away.cfbd_id}.png" if away.cfbd_id else None
        data["home_logo"] = f"logos/{home.cfbd_id}.png" if home.cfbd_id else None
    else:
        # Keep Team instances so templates can use team_logo_url / name / abbr
        data["away_team"] = away
        data["home_team"] = home
    return data


def _competition_ranks(sorted_keys: List[Tuple]) -> List[int]:
    """Competition ranks for items already sorted best-first (reverse sort)."""
    ranks: List[int] = []
    current_rank = 1
    previous_key = None
    for index, key in enumerate(sorted_keys):
        if previous_key is not None and key != previous_key:
            current_rank = index + 1
        ranks.append(current_rank)
        previous_key = key
    return ranks


def _rank_week_stats(stats: List[WeekStat], league_rules: LeagueRules) -> None:
    def sort_key(stat: WeekStat):
        return (
            stat.points,
            _tiebreak_cascade(
                league_rules,
                correct=stat.correct,
                correct_key=stat.correct_key,
                tiebreak_abs_diff=stat.tiebreak_abs_diff,
            ),
        )

    ordered = sorted(stats, key=sort_key, reverse=True)
    keys = [sort_key(s) for s in ordered]
    ranks = _competition_ranks(keys)
    for stat, rank in zip(ordered, ranks):
        stat.rank = rank


def _rank_season_stats(stats: List[SeasonStat], league_rules: LeagueRules) -> None:
    def full_key(stat: SeasonStat):
        return (
            stat.points,
            _tiebreak_cascade(
                league_rules,
                correct=stat.correct,
                correct_key=stat.correct_key,
                tiebreak_abs_diff=None,
            ),
        )

    def adjusted_key(stat: SeasonStat):
        return (
            stat.points - stat.points_dropped,
            _tiebreak_cascade(
                league_rules,
                correct=stat.correct - stat.correct_dropped,
                correct_key=stat.correct_key - stat.correct_key_dropped,
                tiebreak_abs_diff=None,
            ),
        )

    ordered_full = sorted(stats, key=full_key, reverse=True)
    for stat, rank in zip(ordered_full, _competition_ranks([full_key(s) for s in ordered_full])):
        stat.rank = rank

    if league_rules.drop_weeks > 0:
        ordered_adj = sorted(stats, key=adjusted_key, reverse=True)
        for stat, rank in zip(
            ordered_adj, _competition_ranks([adjusted_key(s) for s in ordered_adj])
        ):
            stat.rank_with_drops = rank
    else:
        for stat in stats:
            stat.rank_with_drops = 0


def _apply_drop_weeks(
    weeks: List[WeekStat],
    league_rules: LeagueRules,
) -> Tuple[int, int, int, int, int, int]:
    """
    Return dropped (picks_made, correct, incorrect, ties, correct_key, points)
    from the worst N weeks, matching update_member_season_for_league.
    """
    empty = (0, 0, 0, 0, 0, 0)
    if league_rules.drop_weeks <= 0 or len(weeks) <= league_rules.drop_weeks:
        return empty

    def week_key(week: WeekStat):
        return (
            week.points,
            _tiebreak_cascade(
                league_rules,
                correct=week.correct,
                correct_key=week.correct_key,
                tiebreak_abs_diff=week.tiebreak_abs_diff,
            ),
        )

    worst = sorted(weeks, key=week_key)[: league_rules.drop_weeks]
    return (
        sum(w.picks_made for w in worst),
        sum(w.correct for w in worst),
        sum(w.incorrect for w in worst),
        sum(w.ties for w in worst),
        sum(w.correct_key for w in worst),
        sum(w.points for w in worst),
    )


def _grade_pick_on_game(
    pick: Pick,
    game: Game,
    league_rules: LeagueRules,
    league_game: LeagueGame,
) -> Tuple[Optional[bool], int]:
    is_correct = is_pick_correct(pick, game, league_rules, league_game=league_game)
    points = calculate_pick_points(pick, is_correct, league_rules)
    return is_correct, points


def _build_week_stats_for_user(
    *,
    user_id: int,
    picks: List[Pick],
    final_by_game_id: Dict[int, Tuple[Game, LeagueGame]],
    simulated_by_game_id: Dict[int, Tuple[Game, LeagueGame]],
    league_rules: LeagueRules,
) -> WeekStat:
    stat = WeekStat(user_id=user_id)
    points_guess = None
    points_actual = None
    tiebreak_abs_diff = None

    for pick in picks:
        game_id = pick.game_id
        graded_game = None
        league_game = None

        if game_id in final_by_game_id:
            graded_game, league_game = final_by_game_id[game_id]
        elif game_id in simulated_by_game_id:
            graded_game, league_game = simulated_by_game_id[game_id]
        else:
            continue

        is_correct, pts = _grade_pick_on_game(pick, graded_game, league_rules, league_game)
        stat.picks_made += 1
        if is_correct is True:
            stat.correct += 1
            if pick.is_key_pick:
                stat.correct_key += 1
        elif is_correct is False:
            stat.incorrect += 1
        else:
            stat.ties += 1
        stat.points += pts

        if (
            league_rules.tiebreaker == 2
            and pick.is_total_points_game
            and pick.points_guess is not None
            and graded_game.home_score is not None
            and graded_game.away_score is not None
        ):
            points_guess = pick.points_guess
            points_actual = graded_game.home_score + graded_game.away_score
            tiebreak_abs_diff = abs(points_guess - points_actual)

    stat.points_guess = points_guess
    stat.points_actual = points_actual
    stat.tiebreak_abs_diff = tiebreak_abs_diff
    return stat


def _member_week_to_stat(mw: MemberWeek) -> WeekStat:
    return WeekStat(
        user_id=mw.user_id,
        picks_made=mw.picks_made,
        correct=mw.correct,
        incorrect=mw.incorrect,
        ties=mw.ties,
        correct_key=mw.correct_key,
        points=mw.points,
        points_guess=mw.points_guess,
        points_actual=mw.points_actual,
        tiebreak_abs_diff=mw.tiebreak_abs_diff,
        rank=mw.rank or 0,
    )


def _actual_week_ranks(league: League, week: Week) -> Dict[int, int]:
    rows = MemberWeek.objects.filter(
        league=league,
        week=week,
        user_id__in=LeagueMembership.objects.filter(
            league=league, is_active=True
        ).values_list("user_id", flat=True),
    )
    return {mw.user_id: (mw.rank or 0) for mw in rows}


def _actual_season_ranks(
    league: League,
    season,
    league_rules: LeagueRules,
    *,
    use_drops: bool,
) -> Dict[int, int]:
    rows = MemberSeason.objects.filter(
        league=league,
        season=season,
        user_id__in=LeagueMembership.objects.filter(
            league=league, is_active=True
        ).values_list("user_id", flat=True),
    )
    result = {}
    for ms in rows:
        if use_drops and league_rules.drop_weeks > 0 and ms.rank_with_drops:
            result[ms.user_id] = ms.rank_with_drops
        else:
            result[ms.user_id] = ms.rank or 0
    return result


def get_simulatable_games(
    league: League,
    week: Week,
    league_rules: LeagueRules,
) -> List[dict]:
    """Public list of started, non-final active slate games for the UI."""
    league_games = list(
        LeagueGame.objects.filter(
            league=league,
            is_active=True,
            game__week=week,
        ).select_related("game__home_team", "game__away_team")
    )
    _, started, _ = _classify_league_games(league_games)
    allows_push = bool(
        league_rules.against_the_spread_enabled and not league_rules.force_hooks
    )
    return [
        _serialize_simulatable_game(
            lg,
            against_the_spread=league_rules.against_the_spread_enabled,
            allows_push=allows_push
            and lg.locked_home_spread is not None
            and Decimal(str(lg.locked_home_spread))
            == Decimal(str(lg.locked_home_spread)).to_integral_value(),
            for_json=False,
        )
        for lg in started
    ]


def simulate_standings(
    league: League,
    week: Week,
    league_rules: LeagueRules,
    outcomes: Dict[int, str],
    *,
    use_season_drops: bool = True,
) -> WhatIfResult:
    """
    Compute hypothetical week and season standings for the given outcomes.

    outcomes: mapping of game_id -> 'home' | 'away' | 'push'
    """
    result = WhatIfResult(
        against_the_spread=league_rules.against_the_spread_enabled,
        allows_push=bool(
            league_rules.against_the_spread_enabled and not league_rules.force_hooks
        ),
    )

    league_games = list(
        LeagueGame.objects.filter(
            league=league,
            is_active=True,
            game__week=week,
        ).select_related("game__home_team", "game__away_team")
    )
    final_lgs, started_lgs, unstarted_lgs = _classify_league_games(league_games)
    started_ids = {lg.game_id for lg in started_lgs}
    unstarted_ids = {lg.game_id for lg in unstarted_lgs}
    final_ids = {lg.game_id for lg in final_lgs}

    # Validate outcomes
    normalized: Dict[int, str] = {}
    for raw_id, outcome in outcomes.items():
        try:
            game_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise WhatIfError(f"Invalid game id '{raw_id}'") from exc
        outcome = str(outcome).lower().strip()
        if outcome not in VALID_OUTCOMES:
            raise WhatIfError(f"Invalid outcome '{outcome}' for game {game_id}")
        if game_id in unstarted_ids:
            raise WhatIfError(
                f"Game {game_id} has not started and cannot be simulated"
            )
        if game_id in final_ids:
            raise WhatIfError(f"Game {game_id} is already final")
        if game_id not in started_ids:
            raise WhatIfError(f"Game {game_id} is not on this week's active slate")
        normalized[game_id] = outcome

    result.games = [
        _serialize_simulatable_game(
            lg,
            against_the_spread=league_rules.against_the_spread_enabled,
            allows_push=result.allows_push
            and lg.locked_home_spread is not None
            and Decimal(str(lg.locked_home_spread))
            == Decimal(str(lg.locked_home_spread)).to_integral_value(),
            for_json=True,
        )
        for lg in started_lgs
    ]

    # Build score maps
    final_by_game_id: Dict[int, Tuple[Game, LeagueGame]] = {
        lg.game_id: (lg.game, lg) for lg in final_lgs
    }
    simulated_by_game_id: Dict[int, Tuple[Game, LeagueGame]] = {}
    started_by_id = {lg.game_id: lg for lg in started_lgs}

    for game_id, outcome in normalized.items():
        lg = started_by_id[game_id]
        home_score, away_score = synthesize_scores(
            outcome,
            against_the_spread=league_rules.against_the_spread_enabled,
            locked_home_spread=lg.locked_home_spread,
            force_hooks=league_rules.force_hooks,
        )
        simulated_by_game_id[game_id] = (
            _game_as_final(lg.game, home_score, away_score),
            lg,
        )

    members = list(
        User.objects.filter(
            league_memberships__league=league,
            league_memberships__is_active=True,
        ).distinct()
    )
    member_ids = [m.id for m in members]
    users_by_id = {m.id: m for m in members}

    # All active-slate picks for this week
    week_picks = list(
        filter_active_league_picks(
            Pick.objects.filter(league=league, game__week=week).select_related(
                "game", "picked_team"
            ),
            league,
        )
    )
    picks_by_user: Dict[int, List[Pick]] = {uid: [] for uid in member_ids}
    for pick in week_picks:
        if pick.user_id in picks_by_user:
            picks_by_user[pick.user_id].append(pick)

    hypo_weeks: List[WeekStat] = []
    for user_id in member_ids:
        hypo_weeks.append(
            _build_week_stats_for_user(
                user_id=user_id,
                picks=picks_by_user.get(user_id, []),
                final_by_game_id=final_by_game_id,
                simulated_by_game_id=simulated_by_game_id,
                league_rules=league_rules,
            )
        )

    _rank_week_stats(hypo_weeks, league_rules)
    actual_week_ranks = _actual_week_ranks(league, week)

    for stat in hypo_weeks:
        user = users_by_id[stat.user_id]
        actual_rank = actual_week_ranks.get(stat.user_id) or None
        hypo_rank = stat.rank or None
        delta = None
        if actual_rank and hypo_rank:
            delta = actual_rank - hypo_rank  # positive = moved up
        result.week_standings.append(
            {
                "user_id": user.id,
                "username": user.username,
                "display_name": user.get_full_name() or user.username,
                "wins": stat.correct,
                "losses": stat.incorrect,
                "ties": stat.ties,
                "points": stat.points,
                "correct_key": stat.correct_key,
                "picks_made": stat.picks_made,
                "hypo_rank": hypo_rank,
                "actual_rank": actual_rank,
                "rank_delta": delta,
                "points_guess": stat.points_guess,
                "points_actual": stat.points_actual,
                "tiebreak_abs_diff": stat.tiebreak_abs_diff,
            }
        )
    result.week_standings.sort(key=lambda r: r["hypo_rank"] or 999)

    # Season: prior weeks' stored MemberWeek + this hypo week
    season = week.season
    weeks_with_finals = set(
        LeagueGame.objects.filter(
            league=league,
            game__season=season,
            game__is_final=True,
            is_active=True,
        ).values_list("game__week_id", flat=True)
    )
    # Current week counts toward season if it has finals or any simulation.
    if final_lgs or simulated_by_game_id:
        weeks_with_finals.add(week.id)

    prior_member_weeks = MemberWeek.objects.filter(
        league=league,
        week__season=season,
        user_id__in=member_ids,
        week_id__in=weeks_with_finals,
    ).exclude(week_id=week.id)

    prior_by_user: Dict[int, List[WeekStat]] = {uid: [] for uid in member_ids}
    for mw in prior_member_weeks:
        prior_by_user[mw.user_id].append(_member_week_to_stat(mw))

    hypo_by_user = {s.user_id: s for s in hypo_weeks}
    season_stats: List[SeasonStat] = []

    for user_id in member_ids:
        weeks_for_user = list(prior_by_user.get(user_id, []))
        if week.id in weeks_with_finals:
            weeks_for_user.append(hypo_by_user[user_id])

        season_stat = SeasonStat(user_id=user_id)
        for w in weeks_for_user:
            season_stat.picks_made += w.picks_made
            season_stat.correct += w.correct
            season_stat.incorrect += w.incorrect
            season_stat.ties += w.ties
            season_stat.correct_key += w.correct_key
            season_stat.points += w.points

        (
            season_stat.picks_made_dropped,
            season_stat.correct_dropped,
            season_stat.incorrect_dropped,
            season_stat.ties_dropped,
            season_stat.correct_key_dropped,
            season_stat.points_dropped,
        ) = _apply_drop_weeks(weeks_for_user, league_rules)
        season_stats.append(season_stat)

    _rank_season_stats(season_stats, league_rules)
    actual_season_ranks = _actual_season_ranks(
        league, season, league_rules, use_drops=use_season_drops
    )

    for stat in season_stats:
        user = users_by_id[stat.user_id]
        if use_season_drops and league_rules.drop_weeks > 0 and stat.rank_with_drops:
            hypo_rank = stat.rank_with_drops
            points = stat.points - stat.points_dropped
            wins = stat.correct - stat.correct_dropped
            losses = stat.incorrect - stat.incorrect_dropped
            ties = stat.ties - stat.ties_dropped
        else:
            hypo_rank = stat.rank
            points = stat.points
            wins = stat.correct
            losses = stat.incorrect
            ties = stat.ties

        actual_rank = actual_season_ranks.get(stat.user_id) or None
        delta = None
        if actual_rank and hypo_rank:
            delta = actual_rank - hypo_rank

        result.season_standings.append(
            {
                "user_id": user.id,
                "username": user.username,
                "display_name": user.get_full_name() or user.username,
                "wins": wins,
                "losses": losses,
                "ties": ties,
                "points": points,
                "correct_key": (
                    stat.correct_key - stat.correct_key_dropped
                    if use_season_drops and league_rules.drop_weeks > 0
                    else stat.correct_key
                ),
                "hypo_rank": hypo_rank or None,
                "actual_rank": actual_rank,
                "rank_delta": delta,
            }
        )

    result.season_standings.sort(key=lambda r: r["hypo_rank"] or 999)
    return result
