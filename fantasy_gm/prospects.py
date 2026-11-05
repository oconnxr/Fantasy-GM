"""Minor-league management: eligibility, promote/stash/cut calls, call-up watch."""

from __future__ import annotations

from dataclasses import dataclass

from . import dynasty
from .models import INJURED, MINORS, Player

READY_LEVELS = {"AAA", "MLB"}


@dataclass
class ProspectReport:
    player: Player
    status: str                 # roster status on your team
    eligibility: str            # eligible / warning / lost / unknown
    used: float                 # fraction of the eligibility limit used
    action: str                 # ACTIVATE OR CUT / PROMOTE / SWAP / WATCH / STASH
    reason: str
    dynasty_value: float


def eligibility(p: Player, cfg) -> tuple[str, float]:
    """League rule: real-life status in the minors, AND career+current MLB
    AB <= max_career_ab AND IP <= max_career_ip (both apply to everyone)."""
    fracs = []
    if cfg.max_career_ab:
        fracs.append(p.career_ab / cfg.max_career_ab)
    if cfg.max_career_ip:
        fracs.append(p.career_ip / cfg.max_career_ip)
    if not fracs:
        return "unknown", 0.0
    frac = max(fracs)
    if frac > 1:
        return "lost", frac
    if p.on_mlb_roster:
        return "on MLB roster", frac
    if frac >= cfg.warn_fraction:
        return "warning", frac
    return "eligible", frac


def minor_free_agents(league, mode: str = "balanced", limit: int = 25) -> list[tuple[Player, float]]:
    fas = [(p, league.dyn(p, mode)) for p in league.free_agents()
           if dynasty.is_prospect(p)]
    fas.sort(key=lambda x: x[1], reverse=True)
    return fas[:limit]


def team_report(league, team_id: str) -> list[ProspectReport]:
    cfg = league.cfg
    team = league.teams[team_id]
    mode = team.mode
    status = {r.player_id: r.status for r in team.roster}
    roster = league.team_players(team_id)
    active = [p for p in roster if status[p.id] not in (MINORS, INJURED) and p.has_projection]
    worst_active = {
        side: min((p for p in active if p.is_pitcher == side), key=lambda p: p.value, default=None)
        for side in (True, False)
    }
    roster_full = len(league.counted_roster(team_id)) >= cfg.max_roster
    # Pair each clearly better free agent with one of your weakest prospects.
    swaps: dict[str, tuple[Player, float]] = {}
    if roster_full:
        # Never suggest dropping a ranked prospect for an unranked one.
        mine = sorted((p for p in roster if status[p.id] == MINORS and not dynasty.prospect_fv(p)),
                      key=lambda p: league.dyn(p, mode))
        fas = minor_free_agents(league, mode, len(mine))
        for p, (fa, fa_val) in zip(mine, fas):
            if fa_val > league.dyn(p, mode) * 1.15 + 0.5:
                swaps[p.id] = (fa, fa_val)

    reports = []
    for p in roster:
        is_prospect = status[p.id] == MINORS or (dynasty.is_prospect(p) and not p.has_projection)
        if not is_prospect:
            continue
        elig, used = eligibility(p, cfg)
        dv = league.dyn(p, mode)
        worst = worst_active[p.is_pitcher]
        if elig == "lost" and status[p.id] == MINORS:
            limit = "AB" if p.career_ab > cfg.max_career_ab else "IP"
            used_n = p.career_ab if limit == "AB" else p.career_ip
            cap = cfg.max_career_ab if limit == "AB" else cfg.max_career_ip
            action, reason = "ACTIVATE OR CUT", f"{used_n:.0f} career {limit} > {cap}: no longer minors-eligible"
        elif elig == "on MLB roster" and status[p.id] == MINORS:
            action, reason = "ACTIVATE", "on an MLB active roster, so not minors-eligible right now"
        elif (status[p.id] == MINORS and p.has_projection and worst is not None
              and p.value > worst.value and mode != "rebuilding"):
            action, reason = "PROMOTE", f"projects {p.value:+.1f} vs {worst.name} at {worst.value:+.1f}"
        elif p.id in swaps:
            fa, fa_val = swaps[p.id]
            action, reason = "SWAP", f"for {fa.name} on waivers ({fa_val:.1f} vs {dv:.1f})"
        elif elig == "warning":
            action, reason = "WATCH", f"used {used:.0%} of minors eligibility (130 AB / 50 IP)"
        else:
            action, reason = "STASH", _stash_reason(p, cfg)
        reports.append(ProspectReport(p, status[p.id], elig, used, action, reason, dv))
    reports.sort(key=lambda r: r.dynasty_value, reverse=True)
    return reports


def _stash_reason(p: Player, cfg) -> str:
    bits = []
    if p.fv:
        bits.append(f"FV {p.fv:g}")
    if p.eta:
        bits.append(f"ETA {p.eta}")
    if p.level:
        bits.append(p.level)
    return ", ".join(bits) or "developing"


def callup_watch(league, limit: int = 20, rostered_only: bool = False) -> list[tuple[Player, str | None, float]]:
    """Prospects close to the majors: ETA this season and at AAA (or ETA passed)."""
    out = []
    for p in league.players.values():
        if not dynasty.is_prospect(p):
            continue
        near = (p.eta is not None and p.eta <= league.cfg.season
                and (not p.level or p.level.upper() in READY_LEVELS or p.eta < league.cfg.season))
        if not near:
            continue
        owner = league.owner_of(p.id)
        if rostered_only and owner is None:
            continue
        out.append((p, owner, p.dynasty_value))
    out.sort(key=lambda x: x[2], reverse=True)
    return out[:limit]
