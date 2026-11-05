"""Fantrax league sync.

Fantrax exposes a read-only JSON API under ``/fxea/general/``. It is not
officially documented, so every parser here is defensive: unknown fields are
ignored and missing ones fall back to sensible defaults. Raw responses are
cached under ``data/fantrax/`` so every other command works offline from the
last sync.

Private leagues need a logged-in session. Copy the ``Cookie`` header from a
logged-in browser request to fantrax.com into ``FANTRAX_COOKIE`` or a
``.fantrax_cookie`` file in the project root.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests

from .models import ACTIVE, INJURED, MINORS, RESERVE, DraftPick, Player, RosterEntry, Team

BASE = "https://www.fantrax.com/fxea/general"
CACHE_DIR = Path("data/fantrax")

_STATUS_MAP = {
    "ACTIVE": ACTIVE,
    "RESERVE": RESERVE,
    "BENCH": RESERVE,
    "MINORS": MINORS,
    "MINOR": MINORS,
    "INJURED_RESERVE": INJURED,
    "IR": INJURED,
    "INJURED": INJURED,
}


class FantraxError(RuntimeError):
    pass


def _cookie() -> str | None:
    if os.environ.get("FANTRAX_COOKIE"):
        return os.environ["FANTRAX_COOKIE"]
    p = Path(".fantrax_cookie")
    return p.read_text().strip() if p.exists() else None


class FantraxClient:
    def __init__(self, league_id: str, cache_dir: Path = CACHE_DIR, timeout: float = 30):
        self.league_id = league_id
        self.cache_dir = Path(cache_dir)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "fantasy-gm/0.1"
        if cookie := _cookie():
            self.session.headers["Cookie"] = cookie
        self.timeout = timeout

    def _get(self, endpoint: str, **params: Any) -> Any:
        resp = self.session.get(f"{BASE}/{endpoint}", params=params, timeout=self.timeout)
        if resp.status_code in (401, 403):
            raise FantraxError(
                f"{endpoint}: Fantrax refused access ({resp.status_code}). If the league "
                "is private, set FANTRAX_COOKIE or create .fantrax_cookie."
            )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as e:
            raise FantraxError(f"{endpoint}: response was not JSON") from e
        if isinstance(data, dict) and data.get("error"):
            raise FantraxError(f"{endpoint}: {data['error']}")
        return data

    def sync(self) -> dict[str, Any]:
        """Fetch every endpoint the GM needs and write it to the cache."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        fetched = {
            "league_info": self._get("getLeagueInfo", leagueId=self.league_id),
            "rosters": self._get("getTeamRosters", leagueId=self.league_id),
            "draft_picks": self._get("getDraftPicks", leagueId=self.league_id),
            "players": self._get("getPlayerIds", sport="MLB"),
        }
        try:
            fetched["standings"] = self._get("getStandings", leagueId=self.league_id)
        except (FantraxError, requests.HTTPError):
            fetched["standings"] = None  # not fatal: only used for mode detection
        for name, data in fetched.items():
            (self.cache_dir / f"{name}.json").write_text(json.dumps(data, indent=1))
        return fetched


def load_cache(cache_dir: Path = CACHE_DIR) -> dict[str, Any]:
    out = {}
    for name in ("league_info", "rosters", "draft_picks", "players", "standings"):
        p = Path(cache_dir) / f"{name}.json"
        out[name] = json.loads(p.read_text()) if p.exists() else None
    return out


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------

def _split_positions(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        items = raw
    else:
        items = str(raw).replace("/", ",").split(",")
    return [p.strip().upper() for p in items if p and p.strip()]


def _flip_name(name: str) -> str:
    """Fantrax often returns 'Last, First'."""
    if "," in name:
        last, first = name.split(",", 1)
        return f"{first.strip()} {last.strip()}"
    return name.strip()


def parse_players(raw: Any) -> dict[str, Player]:
    """getPlayerIds -> {fantrax_id: Player}."""
    players: dict[str, Player] = {}
    if not raw:
        return players
    items = raw.values() if isinstance(raw, dict) else raw
    for item in items:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("fantraxId") or item.get("id") or "")
        if not pid:
            continue
        players[pid] = Player(
            id=pid,
            name=_flip_name(str(item.get("name", ""))),
            mlb_team=str(item.get("team", "") or ""),
            positions=_split_positions(item.get("position") or item.get("positions")),
        )
    return players


def parse_teams(league_info: Any, rosters: Any) -> dict[str, Team]:
    teams: dict[str, Team] = {}
    info = (league_info or {}).get("teamInfo") or {}
    for tid, t in (info.items() if isinstance(info, dict) else []):
        teams[str(tid)] = Team(id=str(tid), name=str(t.get("name", tid)))

    roster_map = (rosters or {}).get("rosters") or {}
    for tid, r in (roster_map.items() if isinstance(roster_map, dict) else []):
        tid = str(tid)
        team = teams.setdefault(tid, Team(id=tid, name=str(r.get("teamName", tid))))
        if r.get("teamName"):
            team.name = str(r["teamName"])
        for item in r.get("rosterItems", []) or []:
            pid = str(item.get("id", ""))
            if not pid:
                continue
            status = _STATUS_MAP.get(str(item.get("status", "ACTIVE")).upper(), RESERVE)
            team.roster.append(RosterEntry(pid, status, str(item.get("position", "") or "")))
    return teams


def parse_draft_picks(raw: Any) -> list[DraftPick]:
    picks: list[DraftPick] = []
    if not raw:
        return picks
    for key in ("futureDraftPicks", "currentDraftPicks", "draftPicks"):
        for item in raw.get(key, []) or []:
            try:
                owner = str(item.get("currentOwnerTeamId") or item.get("teamId") or "")
                orig = str(item.get("originalOwnerTeamId") or owner)
                year = int(item.get("year") or item.get("season") or 0)
                rnd = int(item.get("round") or 0)
            except (TypeError, ValueError):
                continue
            if not owner or not rnd:
                continue
            pick_no = item.get("pickInRound") or item.get("pick")
            picks.append(DraftPick(year, rnd, orig, owner, int(pick_no) if pick_no else None))
    return picks


def parse_roster_slots(league_info: Any) -> dict[str, int] | None:
    """Pull active lineup slot counts from league info, if present."""
    constraints = ((league_info or {}).get("rosterInfo") or {}).get("positionConstraints")
    if not isinstance(constraints, dict):
        return None
    slots = {}
    for pos, c in constraints.items():
        n = c.get("maxActive") if isinstance(c, dict) else c
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if n > 0:
            slots[str(pos).upper()] = n
    return slots or None


def parse_eligibility(league_info: Any) -> dict[str, list[str]]:
    """League-specific position eligibility from league info, if present."""
    out: dict[str, list[str]] = {}
    info = (league_info or {}).get("playerInfo") or {}
    for pid, p in (info.items() if isinstance(info, dict) else []):
        if isinstance(p, dict) and p.get("eligiblePos"):
            out[str(pid)] = _split_positions(p["eligiblePos"])
    return out
