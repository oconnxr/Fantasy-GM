"""League configuration loaded from league.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HITTER_SLOTS = {"C", "1B", "2B", "3B", "SS", "OF", "UT", "CI", "MI", "IF", "INF", "DH"}
PITCHER_SLOTS = {"SP", "RP", "P"}

# Which player positions may fill each lineup slot.
SLOT_ELIGIBILITY: dict[str, set[str]] = {
    "C": {"C"},
    "1B": {"1B"},
    "2B": {"2B"},
    "3B": {"3B"},
    "SS": {"SS"},
    "OF": {"OF", "LF", "CF", "RF"},
    "CI": {"1B", "3B"},
    "MI": {"2B", "SS"},
    "IF": {"1B", "2B", "3B", "SS"},
    "INF": {"1B", "2B", "3B", "SS", "INF"},
    "UT": {"C", "1B", "2B", "3B", "SS", "OF", "LF", "CF", "RF", "DH", "UT"},
    "DH": {"C", "1B", "2B", "3B", "SS", "OF", "LF", "CF", "RF", "DH", "UT"},
    "SP": {"SP"},
    "RP": {"RP"},
    "P": {"SP", "RP", "P"},
}

MODES = ("contending", "balanced", "rebuilding")


@dataclass
class LeagueConfig:
    name: str = "Dynasty League"
    fantrax_league_id: str = ""
    teams: int = 16
    season: int = 2027
    my_team_id: str = ""
    hitting_cats: list[str] = field(default_factory=lambda: ["R", "HR", "RBI", "SB", "OBP"])
    pitching_cats: list[str] = field(default_factory=lambda: ["ERA", "W", "SVH", "WHIP", "K"])
    inverse_cats: set[str] = field(default_factory=lambda: {"ERA", "WHIP"})
    slots: dict[str, int] = field(
        default_factory=lambda: {
            "C": 1, "1B": 1, "2B": 1, "3B": 1, "SS": 1, "OF": 3, "UT": 1,
            "SP": 5, "RP": 3, "P": 1,
        }
    )
    bench: int = 6
    minors: int = 10
    injured_reserve: int = 3
    max_career_ab: int = 130
    max_career_ip: int = 50
    warn_fraction: float = 0.8
    horizon_years: int = 5
    discount: dict[str, float] = field(
        default_factory=lambda: {"contending": 0.65, "balanced": 0.80, "rebuilding": 0.92}
    )
    year0_weight: dict[str, float] = field(
        default_factory=lambda: {"contending": 1.25, "balanced": 1.0, "rebuilding": 0.6}
    )
    rookie_draft_rounds: int = 4
    # Fantrax team id -> contending / balanced / rebuilding. Teams not listed
    # are classified automatically from roster strength.
    mode_overrides: dict[str, str] = field(default_factory=dict)

    @property
    def categories(self) -> list[str]:
        return self.hitting_cats + self.pitching_cats

    @property
    def hitter_starters_per_team(self) -> int:
        return sum(n for s, n in self.slots.items() if s in HITTER_SLOTS)

    @property
    def pitcher_starters_per_team(self) -> int:
        return sum(n for s, n in self.slots.items() if s in PITCHER_SLOTS)

    @property
    def active_roster_size(self) -> int:
        return sum(self.slots.values()) + self.bench

    @classmethod
    def load(cls, path: str | Path = "league.toml") -> "LeagueConfig":
        path = Path(path)
        if not path.exists():
            return cls()
        raw = tomllib.loads(path.read_text())
        lg = raw.get("league", {})
        cats = raw.get("categories", {})
        roster = raw.get("roster", {})
        me = raw.get("minors_eligibility", {})
        dyn = raw.get("dynasty", {})
        cfg = cls()
        cfg.name = lg.get("name", cfg.name)
        cfg.fantrax_league_id = lg.get("fantrax_league_id", cfg.fantrax_league_id)
        cfg.teams = int(lg.get("teams", cfg.teams))
        cfg.season = int(lg.get("season", cfg.season))
        cfg.my_team_id = str(lg.get("my_team_id", cfg.my_team_id))
        cfg.hitting_cats = list(cats.get("hitting", cfg.hitting_cats))
        cfg.pitching_cats = list(cats.get("pitching", cfg.pitching_cats))
        cfg.inverse_cats = set(cats.get("inverse", cfg.inverse_cats))
        cfg.slots = {k: int(v) for k, v in roster.get("slots", cfg.slots).items()}
        cfg.bench = int(roster.get("bench", cfg.bench))
        cfg.minors = int(roster.get("minors", cfg.minors))
        cfg.injured_reserve = int(roster.get("injured_reserve", cfg.injured_reserve))
        cfg.max_career_ab = int(me.get("max_career_ab", cfg.max_career_ab))
        cfg.max_career_ip = int(me.get("max_career_ip", cfg.max_career_ip))
        cfg.warn_fraction = float(me.get("warn_fraction", cfg.warn_fraction))
        cfg.horizon_years = int(dyn.get("horizon_years", cfg.horizon_years))
        cfg.discount = {**cfg.discount, **dyn.get("discount", {})}
        cfg.year0_weight = {**cfg.year0_weight, **dyn.get("year0_weight", {})}
        cfg.rookie_draft_rounds = int(dyn.get("rookie_draft_rounds", cfg.rookie_draft_rounds))
        cfg.mode_overrides = {str(k): str(v) for k, v in raw.get("team_modes", {}).items()}
        for tid, mode in cfg.mode_overrides.items():
            if mode not in MODES:
                raise ValueError(f"team_modes.{tid}: '{mode}' must be one of {MODES}")
        return cfg
