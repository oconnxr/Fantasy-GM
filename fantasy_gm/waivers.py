"""Waiver recommendations.

Season mode scores each free agent against your weakest droppable player,
blending this-season value (re-weighted by your category needs) with
dynasty value according to your team mode. Week mode re-runs the lineup
optimiser with the free agent swapped in and reports the change in
expected H2H categories won against this week's opponent.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import dynasty, teamstats
from .lineup import WeekContext, available, optimize
from .models import INJURED, MINORS, Player

SEASON_WEIGHT = {"contending": 0.75, "balanced": 0.5, "rebuilding": 0.25}


@dataclass
class WaiverIdea:
    add: Player
    drop: Player | None
    score: float
    season_gain: float
    dynasty_gain: float


def droppable(league, team_id: str, n: int = 5) -> list[Player]:
    mode = league.teams[team_id].mode
    # Anyone counting toward the roster limit can be dropped, minors included.
    return sorted(league.counted_roster(team_id), key=lambda p: league.dyn(p, mode))[:n]


def season_targets(league, team_id: str, limit: int = 15, prospects: bool = False) -> list[WaiverIdea]:
    mode = league.teams[team_id].mode
    weights = teamstats.need_weights(league, team_id)
    w_season = SEASON_WEIGHT[mode]
    per_year = sum(dynasty.year_weights(league.cfg, mode)) or 1.0
    drops = droppable(league, team_id)
    team = league.teams[team_id]
    open_spot = len(league.counted_roster(team_id)) < league.cfg.max_roster

    ideas = []
    for fa in league.free_agents():
        if not fa.has_projection and not (prospects and dynasty.prospect_fv(fa) is not None):
            continue
        fa_season = teamstats.fit_value(fa, weights) if fa.has_projection else 0.0
        fa_dyn = league.dyn(fa, mode) / per_year
        best: WaiverIdea | None = None
        candidates: list[Player | None] = [None] if open_spot else []
        candidates += drops
        for d in candidates:
            d_season = teamstats.fit_value(d, weights) if d is not None and d.has_projection else 0.0
            d_dyn = league.dyn(d, mode) / per_year if d is not None else 0.0
            s_gain, d_gain = fa_season - d_season, fa_dyn - d_dyn
            score = w_season * s_gain + (1 - w_season) * d_gain
            if best is None or score > best.score:
                best = WaiverIdea(fa, d, score, s_gain, d_gain)
        if best and best.score > 0:
            ideas.append(best)
    ideas.sort(key=lambda i: i.score, reverse=True)
    return ideas[:limit]


def week_targets(league, team_id: str, opp_id: str, ctx: WeekContext | None = None,
                 limit: int = 10, shortlist: int = 40) -> tuple[float, list[tuple[Player, Player, float]]]:
    """Free agents who most raise expected categories won this week."""
    ctx = ctx or WeekContext()
    base_pool = available(league, team_id)
    base = optimize(league, team_id, opp_id, ctx, base_pool).expected_wins
    weights = teamstats.need_weights(league, team_id)
    fas = sorted((p for p in league.free_agents() if p.has_projection),
                 key=lambda p: teamstats.fit_value(p, weights), reverse=True)[:shortlist]
    drops = droppable(league, team_id, 3)
    out = []
    for fa in fas:
        best = None
        for d in drops:
            pool = [p for p in base_pool if p.id != d.id] + [fa]
            gain = optimize(league, team_id, opp_id, ctx, pool).expected_wins - base
            if best is None or gain > best[2]:
                best = (fa, d, gain)
        if best and best[2] > 0.01:
            out.append(best)
    out.sort(key=lambda x: x[2], reverse=True)
    return base, out[:limit]
