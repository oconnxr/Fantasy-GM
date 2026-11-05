"""Core data types shared across the GM modules."""

from __future__ import annotations

from dataclasses import dataclass, field

# Roster statuses, normalised from Fantrax's values.
ACTIVE = "ACTIVE"
RESERVE = "RESERVE"      # bench
MINORS = "MINORS"
INJURED = "INJURED_RESERVE"


@dataclass
class Player:
    id: str                              # Fantrax player id when known, else projection id
    name: str
    mlb_team: str = ""
    positions: list[str] = field(default_factory=list)
    age: float | None = None
    # Season projection. Hitters: PA, AB, H, HR, RBI, SB. Pitchers: IP, ER,
    # BB, HA (hits allowed), W, SV, HLD, K, GS.
    proj: dict[str, float] = field(default_factory=dict)
    # Prospect info (from a prospect list import).
    fv: float | None = None              # future value grade (20-80 scale)
    eta: int | None = None               # expected MLB arrival season
    level: str = ""                      # current minor-league level
    prospect_rank: int | None = None
    career_ab: float = 0.0               # MLB career at-bats (minors eligibility)
    career_ip: float = 0.0               # MLB career innings
    # Computed by valuation.
    value: float = 0.0                   # current-season value over replacement
    cat_values: dict[str, float] = field(default_factory=dict)
    dynasty_value: float = 0.0

    @property
    def is_pitcher(self) -> bool:
        if self.positions:
            return all(p in {"SP", "RP", "P"} for p in self.positions)
        return "IP" in self.proj

    @property
    def is_hitter(self) -> bool:
        return not self.is_pitcher

    @property
    def has_projection(self) -> bool:
        key = "IP" if self.is_pitcher else "PA"
        return self.proj.get(key, 0) > 0

    def stat(self, cat: str) -> float:
        """Projected season value for a scoring category."""
        p = self.proj
        if cat == "AVG":
            return p.get("H", 0) / p["AB"] if p.get("AB") else 0.0
        if cat == "ERA":
            return 9 * p.get("ER", 0) / p["IP"] if p.get("IP") else 0.0
        if cat == "WHIP":
            return (p.get("BB", 0) + p.get("HA", 0)) / p["IP"] if p.get("IP") else 0.0
        if cat == "SVH":
            return p.get("SV", 0) + p.get("HLD", 0)
        return p.get(cat, 0.0)

    def __str__(self) -> str:
        pos = "/".join(self.positions) or ("P" if self.is_pitcher else "H")
        return f"{self.name} ({pos}, {self.mlb_team or 'FA'})"


@dataclass
class RosterEntry:
    player_id: str
    status: str = ACTIVE
    slot: str = ""


@dataclass
class DraftPick:
    year: int
    round: int
    original_team_id: str
    owner_team_id: str
    pick_number: int | None = None       # slot within round when known

    @property
    def label(self) -> str:
        slot = f".{self.pick_number:02d}" if self.pick_number else ""
        return f"{self.year} R{self.round}{slot} (orig {self.original_team_id})"


@dataclass
class Team:
    id: str
    name: str
    roster: list[RosterEntry] = field(default_factory=list)
    picks: list[DraftPick] = field(default_factory=list)
    mode: str = "balanced"               # contending / balanced / rebuilding

    def player_ids(self, statuses: set[str] | None = None) -> list[str]:
        return [r.player_id for r in self.roster if statuses is None or r.status in statuses]
