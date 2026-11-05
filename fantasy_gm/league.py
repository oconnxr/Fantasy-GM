"""League state: Fantrax rosters joined to projections, with values computed."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import dynasty, fantrax, projections
from .config import LeagueConfig
from .models import ACTIVE, INJURED, MINORS, RESERVE, DraftPick, Player, Team
from .projections import norm_name
from .valuation import rank_values, value_players


@dataclass
class League:
    cfg: LeagueConfig
    players: dict[str, Player] = field(default_factory=dict)
    teams: dict[str, Team] = field(default_factory=dict)
    picks: list[DraftPick] = field(default_factory=list)
    ranked_values: list[float] = field(default_factory=list)
    pick_curve: list[float] = field(default_factory=list)
    projected_slot: dict[str, int] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)
    league_info: dict | None = None

    def opponent_this_week(self, team_id: str, today=None) -> str | None:
        period = fantrax.current_period(self.league_info, today)
        return fantrax.opponent(self.league_info, team_id, period)

    # ---- lookups ---------------------------------------------------------

    def owner_of(self, player_id: str) -> str | None:
        for t in self.teams.values():
            if any(r.player_id == player_id for r in t.roster):
                return t.id
        return None

    def rostered_ids(self) -> set[str]:
        return {r.player_id for t in self.teams.values() for r in t.roster}

    def free_agents(self) -> list[Player]:
        owned = self.rostered_ids()
        return [p for pid, p in self.players.items() if pid not in owned]

    def team_players(self, team_id: str, statuses: set[str] | None = None) -> list[Player]:
        t = self.teams[team_id]
        return [self.players[pid] for pid in t.player_ids(statuses) if pid in self.players]

    def find_player(self, query: str) -> Player:
        q = norm_name(query)
        if query in self.players:
            return self.players[query]
        exact = [p for p in self.players.values() if norm_name(p.name) == q]
        if exact:
            # Same-name players: prefer the rostered, more valuable one.
            exact.sort(key=lambda p: (self.owner_of(p.id) is None, -p.dynasty_value))
            return exact[0]
        partial = [p for p in self.players.values() if q in norm_name(p.name)]
        if not partial:
            raise KeyError(f"No player matches '{query}'")
        if len(partial) > 1:
            names = ", ".join(str(p) for p in partial[:6])
            raise KeyError(f"'{query}' is ambiguous: {names}")
        return partial[0]

    def find_team(self, query: str) -> Team:
        if query in self.teams:
            return self.teams[query]
        q = query.lower()
        hits = [t for t in self.teams.values() if q in t.name.lower()]
        if len(hits) != 1:
            raise KeyError(f"Team '{query}' matched {len(hits)} teams")
        return hits[0]

    @property
    def my_team(self) -> Team:
        if not self.cfg.my_team_id or self.cfg.my_team_id not in self.teams:
            raise KeyError("Set my_team_id in league.toml (run `fgm teams` to list ids)")
        return self.teams[self.cfg.my_team_id]

    # ---- values ----------------------------------------------------------

    def dyn(self, p: Player, mode: str = "balanced") -> float:
        return dynasty.dynasty_value(p, self.cfg, self.ranked_values, mode)

    def pick_val(self, pick: DraftPick, mode: str = "balanced") -> float:
        return dynasty.pick_value(pick, self.pick_curve, self.cfg, self.projected_slot, mode)

    def team_strength(self, team_id: str) -> float:
        """Current-season value of the team's best possible active roster."""
        vals = sorted((p.value for p in self.team_players(team_id) if p.has_projection),
                      reverse=True)
        return sum(v for v in vals[: sum(self.cfg.slots.values())] if v > 0)


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------

def _merge(target: Player, src: Player) -> None:
    if src.proj and not target.proj:
        target.proj = dict(src.proj)
    if src.age and not target.age:
        target.age = src.age
    if src.positions and not target.positions:
        target.positions = list(src.positions)
    for attr in ("fv", "eta", "level", "prospect_rank"):
        if getattr(src, attr) and not getattr(target, attr):
            setattr(target, attr, getattr(src, attr))
    if src.mlb_team and not target.mlb_team:
        target.mlb_team = src.mlb_team


def _index(players: dict[str, Player]) -> dict[str, list[Player]]:
    idx: dict[str, list[Player]] = {}
    for p in players.values():
        idx.setdefault(norm_name(p.name), []).append(p)
    return idx


def _match(src: Player, idx: dict[str, list[Player]]) -> Player | None:
    cands = idx.get(norm_name(src.name), [])
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1:
        same_team = [c for c in cands if c.mlb_team and c.mlb_team.upper() == src.mlb_team.upper()]
        if len(same_team) == 1:
            return same_team[0]
        # Two-way or same-name players: match on hitter/pitcher side.
        side = [c for c in same_team or cands if c.is_pitcher == src.is_pitcher]
        if len(side) == 1:
            return side[0]
    return None


def build_league(cfg: LeagueConfig, data_dir: Path = Path("data")) -> League:
    league = League(cfg)
    cache = fantrax.load_cache(Path(data_dir) / "fantrax")

    if cache["players"]:
        league.players = fantrax.parse_players(cache["players"])
        for pid, pos in fantrax.parse_eligibility(cache["league_info"]).items():
            if pid in league.players:
                league.players[pid].positions = pos
    league.league_info = cache["league_info"]
    # Fantrax is the source of truth for settings it reports.
    for key, val in fantrax.parse_settings(cache["league_info"]).items():
        setattr(cfg, key, val)
    league.teams = fantrax.parse_teams(cache["league_info"], cache["rosters"])
    if league.teams:
        cfg.teams = len(league.teams)
    league.picks = fantrax.parse_draft_picks(cache["draft_picks"], cfg.season)
    if league.picks:
        cfg.rookie_draft_rounds = max(p.round for p in league.picks)
    for pick in league.picks:
        if pick.owner_team_id in league.teams:
            league.teams[pick.owner_team_id].picks.append(pick)

    # Join projections onto Fantrax players (or use them directly if no sync).
    imports = projections.load_all(data_dir)
    idx = _index(league.players)
    for group in ("batters", "pitchers", "prospects"):
        for src in imports[group]:
            target = _match(src, idx) if league.players else None
            if target is None:
                if league.players and group != "prospects" and src.proj.get("PA", src.proj.get("IP", 0)) > 100:
                    league.unmatched.append(src.name)
                if src.id in league.players:
                    _merge(league.players[src.id], src)
                else:
                    league.players[src.id] = src
                    idx.setdefault(norm_name(src.name), []).append(src)
            else:
                _merge(target, src)
    for name, (ab, ip) in imports["career"].items():
        for p in idx.get(name, []):
            p.career_ab, p.career_ip = ab, ip

    compute_values(league)
    return league


def compute_values(league: League) -> None:
    cfg = league.cfg
    players = list(league.players.values())
    value_players(players, cfg)
    league.ranked_values = rank_values(players)

    # Team modes: overrides, else by current strength (top/bottom ~30%).
    if league.teams:
        ranked = sorted(league.teams, key=league.team_strength, reverse=True)
        cut = max(1, round(len(ranked) * 0.3))
        for i, tid in enumerate(ranked):
            auto = "contending" if i < cut else "rebuilding" if i >= len(ranked) - cut else "balanced"
            league.teams[tid].mode = cfg.mode_overrides.get(tid, auto)
            # Weakest team picks first in the rookie draft.
            league.projected_slot[tid] = len(ranked) - i

    for p in players:
        p.dynasty_value = league.dyn(p)

    owned = league.rostered_ids()
    available = [p.dynasty_value for p in players
                 if p.id not in owned and dynasty.prospect_fv(p) is not None]
    league.pick_curve = dynasty.pick_value_curve(available, cfg)


__all__ = ["League", "build_league", "compute_values", "ACTIVE", "RESERVE", "MINORS", "INJURED"]
