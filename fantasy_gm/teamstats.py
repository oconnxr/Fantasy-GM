"""Team-level category projections and needs."""

from __future__ import annotations

from typing import Iterable

from .config import LeagueConfig
from .models import MINORS, Player, cat_value


def totals(players: Iterable[Player], cfg: LeagueConfig, scale: float = 1.0) -> dict[str, float]:
    """Projected category totals for a set of players (rates computed from components)."""
    agg: dict[str, float] = {}
    for p in players:
        for k, v in p.proj.items():
            agg[k] = agg.get(k, 0.0) + v * scale
    out = {c: cat_value(agg, c) for c in cfg.categories}
    return out


def contributing_players(players: Iterable[Player], cfg: LeagueConfig) -> list[Player]:
    """The players who would fill a team's active lineup over a season."""
    ps = [p for p in players if p.has_projection]
    hitters = sorted((p for p in ps if p.is_hitter), key=lambda p: p.value, reverse=True)
    pitchers = sorted((p for p in ps if p.is_pitcher), key=lambda p: p.value, reverse=True)
    return hitters[: cfg.hitter_starters_per_team] + pitchers[: cfg.pitcher_starters_per_team]


def team_totals(league, team_id: str, extra: list[Player] = (), remove: set[str] = frozenset()):
    team = league.teams[team_id]
    ids = [r.player_id for r in team.roster if r.status != MINORS and r.player_id not in remove]
    players = [league.players[i] for i in ids if i in league.players] + list(extra)
    return totals(contributing_players(players, league.cfg), league.cfg)


def better(cat: str, a: float, b: float, cfg: LeagueConfig) -> bool:
    return a < b if cat in cfg.inverse_cats else a > b


def category_ranks(league) -> dict[str, dict[str, int]]:
    """team_id -> {category: rank} with 1 = best in the league."""
    all_totals = {tid: team_totals(league, tid) for tid in league.teams}
    ranks: dict[str, dict[str, int]] = {tid: {} for tid in league.teams}
    for c in league.cfg.categories:
        rev = c not in league.cfg.inverse_cats
        order = sorted(all_totals, key=lambda t: all_totals[t][c], reverse=rev)
        for i, tid in enumerate(order):
            ranks[tid][c] = i + 1
    return ranks


def need_weights(league, team_id: str) -> dict[str, float]:
    """>1 for categories the team is weak in, <1 where it already dominates.

    In H2H, being 1st in a category by a mile wastes value; being 14th means
    a marginal gain flips weekly wins.
    """
    cats = league.cfg.categories
    if team_id not in league.teams or len(league.teams) < 2:
        return {c: 1.0 for c in cats}
    ranks = category_ranks(league)[team_id]
    n = len(league.teams)
    mid = (n + 1) / 2
    return {c: 1.0 + 0.5 * (ranks[c] - mid) / (n - mid) for c in cats}


def fit_value(p: Player, weights: dict[str, float]) -> float:
    """Current-season value re-weighted by a team's category needs."""
    return sum(z * weights.get(c, 1.0) for c, z in p.cat_values.items())
