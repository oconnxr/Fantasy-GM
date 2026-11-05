"""Dynasty values: multi-year player value, prospects and draft picks.

A player's dynasty value is the sum of his projected value in each of the
next ``horizon_years`` seasons, aged along a curve, floored at zero (you can
always cut a player), and discounted by how much the owning team cares about
the future: contenders discount steeply, rebuilders barely at all.

Prospects without an MLB projection are valued from their FV grade. The
grade maps to the value of the MLB player at a matching overall rank, so
prospect values automatically stay on the same scale as everyone else,
then get multiplied by a hit rate and pushed back to their ETA.
"""

from __future__ import annotations

from .config import LeagueConfig
from .models import DraftPick, Player

# Value relative to peak, by age.
HITTER_CURVE = {20: 0.72, 21: 0.77, 22: 0.82, 23: 0.87, 24: 0.92, 25: 0.97, 26: 1.0,
                27: 1.0, 28: 1.0, 29: 0.99, 30: 0.96, 31: 0.92, 32: 0.87, 33: 0.81,
                34: 0.74, 35: 0.67, 36: 0.59, 37: 0.50, 38: 0.41, 39: 0.33, 40: 0.25}
PITCHER_CURVE = {20: 0.74, 21: 0.79, 22: 0.84, 23: 0.89, 24: 0.94, 25: 0.98, 26: 1.0,
                 27: 1.0, 28: 0.99, 29: 0.97, 30: 0.94, 31: 0.89, 32: 0.83, 33: 0.76,
                 34: 0.69, 35: 0.61, 36: 0.53, 37: 0.45, 38: 0.37, 39: 0.30, 40: 0.23}

# Prospect FV -> overall rank of an MLB player with comparable peak value in
# a 16-team league (scaled for other sizes), and the chance he gets there.
FV_RANK = [(80, 1), (70, 12), (65, 30), (60, 70), (55, 140), (50, 240), (45, 360), (40, 520)]
FV_HIT_RATE = [(80, 0.9), (70, 0.8), (65, 0.7), (60, 0.6), (55, 0.5), (50, 0.4), (45, 0.3), (40, 0.2)]

# Typical value of a solid regular, used to age players near replacement level.
SCALE = 3.0
# Even fringe prospects hold some lottery-ticket value.
PROSPECT_PEAK_FLOOR = 0.3


def _interp(table: list[tuple[float, float]], x: float) -> float:
    table = sorted(table)
    if x <= table[0][0]:
        return table[0][1]
    if x >= table[-1][0]:
        return table[-1][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return table[-1][1]


def age_factor(age: float, pitcher: bool) -> float:
    curve = PITCHER_CURVE if pitcher else HITTER_CURVE
    a = int(round(age))
    if a < 20:
        return curve[20]
    if a > 40:
        return max(0.1, curve[40] - 0.08 * (a - 40))
    return curve[a]


def year_weights(cfg: LeagueConfig, mode: str) -> list[float]:
    d = cfg.discount[mode]
    w = [d ** t for t in range(cfg.horizon_years)]
    w[0] *= cfg.year0_weight[mode]
    return w


def depth_offset(cfg: LeagueConfig, ranked_values: list[float], pitcher: bool | None = None) -> float:
    """How far below startable replacement a rostered player can be and still
    hold value. Uses the league's actual roster depth per hitter/pitcher
    group when known, else the last player on a full active roster + bench."""
    if pitcher is not None and cfg.depth_offsets:
        return cfg.depth_offsets["P" if pitcher else "H"]
    if not ranked_values:
        return 0.0
    rank = cfg.teams * (sum(cfg.slots.values()) + cfg.bench)
    return max(0.0, -ranked_values[min(rank, len(ranked_values)) - 1])


def mlb_value_by_year(p: Player, cfg: LeagueConfig, offset: float = 0.0) -> list[float]:
    base = p.value + offset
    age = p.age or 27
    f0 = age_factor(age, p.is_pitcher)
    out = []
    for t in range(cfg.horizon_years):
        ratio = age_factor(age + t, p.is_pitcher) / f0
        out.append(max(0.0, base + (ratio - 1) * max(base, SCALE)))
    return out


def is_prospect(p: Player) -> bool:
    return prospect_fv(p) is not None or p.prospect_value is not None


# Overall prospect rank -> FV-equivalent grade.
RANK_FV = [(1, 70), (5, 65), (15, 60), (40, 57.5), (100, 55), (200, 50), (400, 45)]
UNRANKED_FV = 50     # unranked prospects: well outside a top-100 list


def prospect_fv(p: Player) -> float | None:
    if p.fv:
        return p.fv
    if p.prospect_rank:
        return _interp(RANK_FV, p.prospect_rank)
    return None


def prospect_value_by_year(p: Player, cfg: LeagueConfig, ranked_values: list[float]) -> list[float]:
    fv = prospect_fv(p)
    if fv is None or not ranked_values:
        return [0.0] * cfg.horizon_years
    rank = max(1, int(round(_interp(FV_RANK, fv) * cfg.teams / 16)))
    peak = ranked_values[min(rank, len(ranked_values)) - 1]
    peak += depth_offset(cfg, ranked_values, p.is_pitcher)
    expected_peak = max(PROSPECT_PEAK_FLOOR, peak) * _interp(FV_HIT_RATE, fv)
    eta = p.eta or (cfg.season + 2)
    age_now = p.age or 22
    out = []
    for t in range(cfg.horizon_years):
        year = cfg.season + t
        if year < eta:
            out.append(0.0)
            continue
        ramp = 0.5 if year == eta else 1.0
        # FV describes peak; scale down for seasons before age 26.
        out.append(expected_peak * ramp * age_factor(age_now + t, p.is_pitcher))
    return out


def stats_prospect_by_year(p: Player, cfg: LeagueConfig, offset: float = 0.0) -> list[float]:
    """Value from the minor-league-stats model: full-season MLE line at ETA,
    developed along the age curve, times the chance he gets there."""
    if p.prospect_value is None or not p.hit_rate:
        return [0.0] * cfg.horizon_years
    base = p.prospect_value + offset
    age = p.age or 22
    f0 = age_factor(age, p.is_pitcher)
    eta = p.eta or (cfg.season + 2)
    out = []
    for t in range(cfg.horizon_years):
        year = cfg.season + t
        if year < eta:
            out.append(0.0)
            continue
        ratio = age_factor(age + t, p.is_pitcher) / f0
        v = max(0.0, base + (ratio - 1) * max(base, SCALE))
        out.append(v * p.hit_rate * (0.5 if year == eta else 1.0))
    return out


def value_by_year(p: Player, cfg: LeagueConfig, ranked_values: list[float]) -> list[float]:
    offset = depth_offset(cfg, ranked_values, p.is_pitcher)
    years = mlb_value_by_year(p, cfg, offset) if p.has_projection else [0.0] * cfg.horizon_years
    if prospect_fv(p) is not None:
        years = [max(a, b) for a, b in zip(years, prospect_value_by_year(p, cfg, ranked_values))]
    elif p.prospect_value is not None:
        # Scouting grades (FV) override the stats model when both exist.
        stats_years = stats_prospect_by_year(p, cfg, offset)
        if cfg.has_rankings:
            # A prospect left off the ranking list shouldn't out-value one on it.
            ghost = Player(id="", name="", positions=p.positions, age=p.age, eta=p.eta, fv=UNRANKED_FV)
            cap = prospect_value_by_year(ghost, cfg, ranked_values)
            stats_years = [min(a, b) for a, b in zip(stats_years, cap)]
        years = [max(a, b) for a, b in zip(years, stats_years)]
    return years


def dynasty_value(p: Player, cfg: LeagueConfig, ranked_values: list[float], mode: str = "balanced") -> float:
    return sum(v * w for v, w in zip(value_by_year(p, cfg, ranked_values), year_weights(cfg, mode)))


# --------------------------------------------------------------------------
# Draft picks
# --------------------------------------------------------------------------

ROOKIE_DRAFT_MAX_AGE = 23


def pick_value_curve(available_prospects: list[float], cfg: LeagueConfig) -> list[float]:
    """Value of each overall pick in a rookie draft.

    Pick k is worth the k-th best prospect not yet rostered. If there aren't
    enough unrostered prospects, extend the curve with exponential decay.
    """
    n = cfg.teams * cfg.rookie_draft_rounds
    vals = sorted(available_prospects, reverse=True)[:n]
    if not vals:
        vals = [3.0]
    while len(vals) < n:
        vals.append(vals[-1] * 0.94)
    return vals


def pick_value(pick: DraftPick, curve: list[float], cfg: LeagueConfig,
               projected_slot: dict[str, int], mode: str = "balanced") -> float:
    slot = pick.pick_number or projected_slot.get(pick.original_team_id, (cfg.teams + 1) // 2)
    overall = (pick.round - 1) * cfg.teams + slot
    v = curve[min(overall, len(curve)) - 1]
    years_out = max(0, pick.year - cfg.season)
    # Uncertainty about future slot and class, plus the team's time preference.
    v *= 0.92 ** years_out
    v *= cfg.discount[mode] ** years_out / cfg.discount["balanced"] ** years_out
    return v
