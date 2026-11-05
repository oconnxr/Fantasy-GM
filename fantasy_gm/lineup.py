"""Weekly H2H lineup optimiser.

Projects each player's week from his season projection, models every
category as a normal distribution, and picks the legal lineup that
maximises the expected number of categories won against this week's
opponent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import HITTER_SLOTS, SLOT_ELIGIBILITY
from .models import INJURED, MINORS, Player

SEASON_DAYS = 183
# Variance inflation over Poisson for weekly counting stats.
DISPERSION = 1.6


@dataclass
class WeekContext:
    hitter_games: dict[str, float] = field(default_factory=dict)  # player id -> games this week
    starts: dict[str, int] = field(default_factory=dict)          # SP id -> starts this week
    default_games: float = 6.0
    default_starts: int = 1


def weekly_line(p: Player, ctx: WeekContext) -> dict[str, float]:
    pr = p.proj
    if p.is_pitcher:
        gs = pr.get("GS", 0)
        if "SP" in p.positions and gs > 0:
            n = ctx.starts.get(p.id, ctx.default_starts)
            # Per-start production; relief appearances folded in.
            return {k: v * n / gs for k, v in pr.items() if k != "GS"}
        frac = 7 / SEASON_DAYS
        return {k: v * frac for k, v in pr.items() if k != "GS"}
    games = ctx.hitter_games.get(p.id, ctx.default_games)
    return {k: v * games / 162 for k, v in pr.items()}


def _sum(lines: list[dict[str, float]]) -> dict[str, float]:
    agg: dict[str, float] = {}
    for line in lines:
        for k, v in line.items():
            agg[k] = agg.get(k, 0.0) + v
    return agg


def cat_dist(agg: dict[str, float], cat: str) -> tuple[float, float]:
    """(mean, variance) of a weekly category total."""
    if cat == "AVG":
        ab, h = agg.get("AB", 0), agg.get("H", 0)
        if not ab:
            return 0.0, 1.0
        avg = h / ab
        return avg, max(avg * (1 - avg) / ab, 1e-6)
    if cat == "ERA":
        ip, er = agg.get("IP", 0), agg.get("ER", 0)
        if not ip:
            return 99.0, 1.0
        return 9 * er / ip, 81 * er * DISPERSION / ip ** 2 + 1e-6
    if cat == "WHIP":
        ip, br = agg.get("IP", 0), agg.get("BB", 0) + agg.get("HA", 0)
        if not ip:
            return 9.0, 1.0
        return br / ip, br * DISPERSION / ip ** 2 + 1e-6
    mean = agg.get("SV", 0) + agg.get("HLD", 0) if cat == "SVH" else agg.get(cat, 0)
    return mean, max(mean * DISPERSION, 0.25)


def win_probs(mine: dict[str, float], theirs: dict[str, float], cfg) -> dict[str, float]:
    out = {}
    for c in cfg.categories:
        m1, v1 = cat_dist(mine, c)
        m2, v2 = cat_dist(theirs, c)
        z = (m1 - m2) / math.sqrt(v1 + v2)
        if c in cfg.inverse_cats:
            z = -z
        out[c] = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return out


# --------------------------------------------------------------------------
# Lineup legality: bipartite matching of players to slot instances
# --------------------------------------------------------------------------

def _slot_list(cfg) -> list[str]:
    return [s for s, n in cfg.slots.items() for _ in range(n)]


def _eligible(p: Player, slot: str) -> bool:
    allowed = SLOT_ELIGIBILITY.get(slot, {slot})
    if slot in HITTER_SLOTS and p.is_pitcher:
        return False
    return bool(allowed & set(p.positions)) or (slot in ("UT", "DH") and p.is_hitter)


def assign(players: list[Player], slots: list[str]) -> dict[int, Player] | None:
    """Slot index -> player covering every player, or None if not legal."""
    match: dict[int, Player] = {}

    def try_place(p: Player, seen: set[int]) -> bool:
        for i, s in enumerate(slots):
            if i in seen or not _eligible(p, s):
                continue
            seen.add(i)
            if i not in match or try_place(match[i], seen):
                match[i] = p
                return True
        return False

    for p in players:
        if not try_place(p, set()):
            return None
    return match


@dataclass
class LineupResult:
    starters: dict[str, list[Player]]
    bench: list[Player]
    probs: dict[str, float]
    my_totals: dict[str, float]
    opp_totals: dict[str, float]

    @property
    def expected_wins(self) -> float:
        return sum(self.probs.values())


def _best_value_lineup(players: list[Player], slots: list[str]) -> list[Player]:
    chosen: list[Player] = []
    for p in sorted(players, key=lambda p: p.value, reverse=True):
        if len(chosen) >= len(slots):
            break
        if assign(chosen + [p], slots) is not None:
            chosen.append(p)
    return chosen


def available(league, team_id: str) -> list[Player]:
    team = league.teams[team_id]
    return [league.players[r.player_id] for r in team.roster
            if r.status not in (MINORS, INJURED) and r.player_id in league.players
            and league.players[r.player_id].has_projection]


def optimize(league, team_id: str, opp_id: str | None, ctx: WeekContext | None = None,
             pool: list[Player] | None = None) -> LineupResult:
    cfg = league.cfg
    ctx = ctx or WeekContext()
    slots = _slot_list(cfg)
    pool = pool if pool is not None else available(league, team_id)

    if opp_id:
        opp = _best_value_lineup(available(league, opp_id), slots)
    else:
        opp = []
    opp_agg = _sum([weekly_line(p, ctx) for p in opp]) if opp else None

    lines = {p.id: weekly_line(p, ctx) for p in pool}

    def score(lineup: list[Player]) -> float:
        agg = _sum([lines[p.id] for p in lineup])
        if opp_agg is None:
            return sum(p.value for p in lineup)
        return sum(win_probs(agg, opp_agg, cfg).values())

    current = _best_value_lineup(pool, slots)
    best = score(current)
    improved = True
    while improved:  # local search: swaps and additions that raise expected wins
        improved = False
        bench = [p for p in pool if p not in current]
        moves = [current + [b] for b in bench if len(current) < len(slots)]
        moves += [current[:i] + current[i + 1:] for i in range(len(current))]
        moves += [current[:i] + [b] + current[i + 1:] for i in range(len(current)) for b in bench]
        for cand in moves:
            s = score(cand)
            if s > best + 1e-9 and assign(cand, slots) is not None:
                current, best, improved = cand, s, True
                break

    match = assign(current, slots) or {}
    starters: dict[str, list[Player]] = {}
    for i, p in sorted(match.items()):
        starters.setdefault(slots[i], []).append(p)
    my_agg = _sum([lines[p.id] for p in current])
    probs = win_probs(my_agg, opp_agg, cfg) if opp_agg else {}
    totals = lambda agg: {c: cat_dist(agg, c)[0] for c in cfg.categories}
    return LineupResult(
        starters=starters,
        bench=[p for p in pool if p not in current],
        probs=probs,
        my_totals=totals(my_agg),
        opp_totals=totals(opp_agg) if opp_agg else {},
    )
