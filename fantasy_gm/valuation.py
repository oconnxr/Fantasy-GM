"""Current-season player values for H2H categories.

Each category is converted to a z-score against the pool of players good
enough to start in a league this size (teams x starting slots). Rate stats
are weighted by playing time: AVG uses hits above the pool average, ERA and
WHIP use runs/baserunners saved against the pool average. A player's value
is his total z-score minus replacement level -- the best player who would
still be on waivers -- with a lower replacement level at scarce positions.
"""

from __future__ import annotations

import statistics

from .config import LeagueConfig
from .models import Player

HITTER_POSITIONS = ("C", "1B", "2B", "3B", "SS", "OF")


def _marginal(p: Player, cat: str, pool_rates: dict[str, float]) -> float:
    """A category contribution on a scale where more is always better."""
    pr = p.proj
    if cat == "AVG":
        return pr.get("H", 0) - pr.get("AB", 0) * pool_rates["AVG"]
    if cat == "ERA":
        return pool_rates["ERA"] * pr.get("IP", 0) / 9 - pr.get("ER", 0)
    if cat == "WHIP":
        return pool_rates["WHIP"] * pr.get("IP", 0) - (pr.get("BB", 0) + pr.get("HA", 0))
    if cat in pool_rates:  # some other rate stat we don't model: treat as counting
        return p.stat(cat)
    return p.stat(cat)


def _pool_rates(pool: list[Player]) -> dict[str, float]:
    ab = sum(p.proj.get("AB", 0) for p in pool)
    ip = sum(p.proj.get("IP", 0) for p in pool)
    return {
        "AVG": sum(p.proj.get("H", 0) for p in pool) / ab if ab else 0.25,
        "ERA": 9 * sum(p.proj.get("ER", 0) for p in pool) / ip if ip else 4.0,
        "WHIP": sum(p.proj.get("BB", 0) + p.proj.get("HA", 0) for p in pool) / ip if ip else 1.25,
    }


def _score_group(players: list[Player], cats: list[str], pool_size: int, playing_time: str) -> None:
    if not players:
        return
    totals = {p.id: p.proj.get(playing_time, 0) for p in players}
    for _ in range(3):  # pool depends on values; a few passes converge
        pool = sorted(players, key=lambda p: totals[p.id], reverse=True)[:pool_size]
        rates = _pool_rates(pool)
        stats = {}
        for cat in cats:
            vals = [_marginal(p, cat, rates) for p in pool]
            mean = statistics.fmean(vals)
            sd = statistics.pstdev(vals) or 1.0
            stats[cat] = (mean, sd)
        for p in players:
            p.cat_values = {
                c: (_marginal(p, c, rates) - stats[c][0]) / stats[c][1] for c in cats
            }
            totals[p.id] = sum(p.cat_values.values())
    for p in players:
        p.value = totals[p.id]


def _replacement(players: list[Player], n: int) -> float:
    ranked = sorted((p.value for p in players), reverse=True)
    if not ranked:
        return 0.0
    if len(ranked) <= n:
        return ranked[-1]
    return ranked[n]


def value_players(players: list[Player], cfg: LeagueConfig) -> None:
    """Set ``value`` and ``cat_values`` on every player with a projection."""
    hitters = [p for p in players if p.is_hitter and p.proj.get("PA", 0) > 0]
    pitchers = [p for p in players if p.is_pitcher and p.proj.get("IP", 0) > 0]
    n_h = cfg.teams * cfg.hitter_starters_per_team
    n_p = cfg.teams * cfg.pitcher_starters_per_team

    _score_group(hitters, cfg.hitting_cats, n_h, "PA")
    _score_group(pitchers, cfg.pitching_cats, n_p, "IP")

    h_repl = _replacement(hitters, n_h)
    pos_repl = {}
    for pos in HITTER_POSITIONS:
        count = cfg.slots.get(pos, 0)
        if not count:
            continue
        eligible = [p for p in hitters if pos in p.positions
                    or (pos == "OF" and {"LF", "CF", "RF"} & set(p.positions))]
        if eligible:
            pos_repl[pos] = min(h_repl, _replacement(eligible, cfg.teams * count))
    for p in hitters:
        repl = min([h_repl] + [pos_repl[x] for x in p.positions if x in pos_repl])
        p.value -= repl

    p_repl = _replacement(pitchers, n_p)
    for p in pitchers:
        p.value -= p_repl


def rank_values(players: list[Player]) -> list[float]:
    """All current values, best first (used to calibrate prospect values)."""
    return sorted((p.value for p in players if p.has_projection), reverse=True)
