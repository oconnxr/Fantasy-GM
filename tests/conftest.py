"""A small synthetic league for tests. Names and numbers are made up."""

from __future__ import annotations

import random

import pytest

from fantasy_gm.config import LeagueConfig
from fantasy_gm.league import League, compute_values
from fantasy_gm.models import ACTIVE, MINORS, RESERVE, DraftPick, Player, RosterEntry, Team

HIT_POS = ["C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"]


def make_hitter(i: int, rng: random.Random, quality: float, age: float = 27, pos: str | None = None) -> Player:
    pa = 450 + 200 * quality
    ab = pa * 0.89
    avg = 0.235 + 0.04 * quality + rng.uniform(-0.01, 0.01)
    return Player(
        id=f"h{i}", name=f"Hitter {i:03d}", mlb_team="TST",
        positions=[pos or HIT_POS[i % len(HIT_POS)]], age=age,
        proj={"PA": pa, "AB": ab, "H": ab * avg, "HR": 8 + 30 * quality + rng.uniform(-3, 3),
              "R": 45 + 55 * quality, "RBI": 40 + 60 * quality, "SB": rng.uniform(0, 25) * quality + 2,
              "BB": pa * (0.06 + 0.05 * quality), "HBP": 5, "SF": 4},
    )


def make_pitcher(i: int, rng: random.Random, quality: float, sp: bool, age: float = 27) -> Player:
    if sp:
        ip, gs, sv, hld = 120 + 70 * quality, 22 + 10 * quality, 0, 0
        w, k = 6 + 9 * quality, ip * (0.85 + 0.35 * quality)
    else:
        ip, gs, sv, hld = 55 + 15 * quality, 0, rng.choice([0, 5, 30 * quality]), 10 * quality
        w, k = 3, ip * (1.0 + 0.3 * quality)
    era = 4.8 - 1.8 * quality
    whip = 1.40 - 0.30 * quality
    return Player(
        id=f"p{i}", name=f"Pitcher {i:03d}", mlb_team="TST",
        positions=["SP" if sp else "RP"], age=age,
        proj={"IP": ip, "ER": era * ip / 9, "BBA": whip * ip * 0.3, "HA": whip * ip * 0.7,
              "W": w, "SV": sv, "HLD": hld, "K": k, "GS": gs},
    )


@pytest.fixture
def cfg() -> LeagueConfig:
    c = LeagueConfig()
    c.teams = 4
    c.season = 2027
    c.my_team_id = "T1"
    c.slots = {"C": 1, "1B": 1, "2B": 1, "3B": 1, "SS": 1, "OF": 3, "UT": 1, "SP": 5, "RP": 3, "P": 1}
    c.bench = 4
    c.minors = 3
    return c


@pytest.fixture
def league(cfg) -> League:
    rng = random.Random(7)
    players: dict[str, Player] = {}
    for i in range(80):
        p = make_hitter(i, rng, quality=rng.random(), age=rng.uniform(22, 36))
        players[p.id] = p
    for i in range(70):
        p = make_pitcher(i, rng, quality=rng.random(), sp=i % 2 == 0, age=rng.uniform(22, 36))
        players[p.id] = p
    for i in range(20):
        pid = f"m{i}"
        players[pid] = Player(id=pid, name=f"Prospect {i:03d}", positions=["SS" if i % 2 else "SP"],
                              age=19 + i % 4, fv=65 - i, eta=2027 + i % 4, level="AA")

    lg = League(cfg, players=players)
    hitters = sorted((p for p in players.values() if p.id.startswith("h")), key=lambda p: p.id)
    pitchers = sorted((p for p in players.values() if p.id.startswith("p")), key=lambda p: p.id)
    pros = [players[f"m{i}"] for i in range(20)]
    for t in range(4):
        tid = f"T{t + 1}"
        team = Team(tid, f"Team {t + 1}")
        roster_h = hitters[t * 14:(t + 1) * 14]
        roster_p = pitchers[t * 13:(t + 1) * 13]
        for p in roster_h[:12] + roster_p[:12]:
            team.roster.append(RosterEntry(p.id, ACTIVE))
        for p in roster_h[12:] + roster_p[12:]:
            team.roster.append(RosterEntry(p.id, RESERVE))
        for p in pros[t * 3:(t + 1) * 3]:
            team.roster.append(RosterEntry(p.id, MINORS))
        for year in (2027, 2028):
            for rnd in (1, 2):
                pick = DraftPick(year, rnd, tid, tid)
                team.picks.append(pick)
                lg.picks.append(pick)
        lg.teams[tid] = team
    compute_values(lg)
    return lg
