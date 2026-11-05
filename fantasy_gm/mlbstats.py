"""Projections built from the public MLB Stats API.

MLB players get a Marcel projection: the last three seasons weighted 5/4/3
(hitters) or 3/2/1 (pitchers), regressed toward league average, adjusted
for age, with playing time from recent seasons.

Minor leaguers' stats are translated to MLB-equivalent rates with level
factors (MLEs) and folded into the same weighted average at reduced
weight, regressed toward a below-average baseline. Prospects also get a
full-season line (600 PA / 150 or 65 IP) that the dynasty model values at
the player's ETA, discounted by a level- and age-based success rate. This
stands in for scouting grades; a prospects.csv with FV grades still wins.
"""

from __future__ import annotations

import json
import time
from datetime import date
from collections import defaultdict
from pathlib import Path
from typing import Any

import requests

from .models import Player
from .projections import norm_name

BASE = "https://statsapi.mlb.com/api/v1"
LEVELS = {1: "MLB", 11: "AAA", 12: "AA", 13: "A+", 14: "A"}

HIT_WEIGHTS = (5, 4, 3)
PIT_WEIGHTS = (3, 2, 1)
MILB_WEIGHT = 0.6            # minor-league seasons count less than MLB ones
REG_PA = 1200
REG_IP = 134

# Share of MLB production a hitter keeps when moving up from each level.
HIT_FACTOR = {"MLB": 1.0, "AAA": 0.87, "AA": 0.82, "A+": 0.76, "A": 0.70}
# Pitchers: multiplier on runs/hits/walks allowed, and on strikeouts.
PIT_BAD = {"MLB": 1.0, "AAA": 1.12, "AA": 1.18, "A+": 1.25, "A": 1.32}
PIT_K = {"MLB": 1.0, "AAA": 0.92, "AA": 0.88, "A+": 0.84, "A": 0.80}

PROSPECT_ETA_OFFSET = {"MLB": 0, "AAA": 0, "AA": 1, "A+": 2, "A": 3}
# Chance a typical-age prospect at each level becomes the player his
# full-season MLE line describes; young-for-level players get a big boost.
PROSPECT_HIT_RATE = {"MLB": 0.6, "AAA": 0.45, "AA": 0.38, "A+": 0.30, "A": 0.24}
PROSPECT_MAX_AGE = 25        # older minor leaguers are valued on MLB projection only
# Typical age at each level, in projection-season terms (season age + 1).
TYPICAL_AGE = {"MLB": 28, "AAA": 26, "AA": 24.5, "A+": 23, "A": 22}

HIT_KEYS = {"PA": "plateAppearances", "AB": "atBats", "H": "hits", "R": "runs",
            "HR": "homeRuns", "RBI": "rbi", "SB": "stolenBases", "BB": "baseOnBalls",
            "HBP": "hitByPitch", "SF": "sacFlies"}
HIT_GOOD = ("H", "R", "HR", "RBI", "SB", "BB", "HBP")
PIT_KEYS = {"ER": "earnedRuns", "BBA": "baseOnBalls", "HA": "hits", "W": "wins",
            "SV": "saves", "HLD": "holds", "K": "strikeOuts", "GS": "gamesStarted",
            "G": "gamesPitched"}
PIT_BAD_KEYS = ("ER", "BBA", "HA")

ABBR_ALIASES = {"CHW": "CWS", "ARI": "AZ", "OAK": "ATH", "SDP": "SD", "SFG": "SF",
                "KCR": "KC", "TBR": "TB", "WAS": "WSH", "WSN": "WSH", "LA": "LAD"}


def canon_abbr(abbr: str) -> str:
    a = (abbr or "").upper().strip()
    return ABBR_ALIASES.get(a, a)


def _ip(v: Any) -> float:
    """'123.1' innings means 123 and one third."""
    try:
        whole, _, frac = str(v).partition(".")
        return int(whole or 0) + int(frac or 0) / 3
    except ValueError:
        return 0.0


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

class MLBClient:
    def __init__(self, cache_dir: Path, timeout: float = 60):
        self.cache_dir = Path(cache_dir)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "fantasy-gm/0.1"
        self.timeout = timeout

    def _get(self, path: str, **params: Any) -> Any:
        for attempt in range(4):
            try:
                r = self.session.get(f"{BASE}/{path}", params=params, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 3:
                    raise
                time.sleep(2 ** (attempt + 1))
        return None

    def fetch_stats(self, seasons: list[int]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for group in ("hitting", "pitching"):
            for sport in LEVELS:
                for season in seasons:
                    data = self._get("stats", stats="season", group=group, season=season,
                                     sportId=sport, playerPool="ALL", limit=10000)
                    splits = (data.get("stats") or [{}])[0].get("splits", [])
                    slim = [{
                        "id": s["player"]["id"], "name": s["player"]["fullName"],
                        "team": (s.get("team") or {}).get("id"), "pos": (s.get("position") or {}).get("abbreviation"),
                        "stat": s.get("stat", {}),
                    } for s in splits if s.get("player")]
                    (self.cache_dir / f"{group}_{sport}_{season}.json").write_text(json.dumps(slim))
        teams = self._get("teams", sportIds="1,11,12,13,14,16", season=max(seasons))
        slim = {t["id"]: {"abbr": t.get("abbreviation", ""), "parent": t.get("parentOrgId"),
                          "sport": (t.get("sport") or {}).get("id")} for t in teams.get("teams", [])}
        (self.cache_dir / "teams.json").write_text(json.dumps(slim))

    def fetch_active_rosters(self) -> None:
        """MLB active-roster ids (real-life "not in the minors"), with the date."""
        teams = self._get("teams", sportId=1).get("teams", [])
        ids = []
        for t in teams:
            data = self._get(f"teams/{t['id']}/roster", rosterType="active")
            ids += [r["person"]["id"] for r in data.get("roster", []) if r.get("person")]
        (self.cache_dir / "active_rosters.json").write_text(
            json.dumps({"date": date.today().isoformat(), "ids": sorted(set(ids))}))

    def fetch_people(self, ids: list[int]) -> None:
        """Birth dates and MLB career totals (for minors eligibility)."""
        out = {}
        ids = sorted(set(ids))
        for i in range(0, len(ids), 100):
            chunk = ",".join(map(str, ids[i:i + 100]))
            data = self._get("people", personIds=chunk,
                             hydrate="stats(group=[hitting,pitching],type=[career])")
            for p in data.get("people", []):
                rec = {"birthDate": p.get("birthDate"), "ab": 0.0, "ip": 0.0,
                       "pos": (p.get("primaryPosition") or {}).get("abbreviation")}
                for st in p.get("stats", []):
                    stat = (st.get("splits") or [{}])[0].get("stat", {})
                    if st.get("group", {}).get("displayName") == "hitting":
                        rec["ab"] = float(stat.get("atBats", 0) or 0)
                    else:
                        rec["ip"] = _ip(stat.get("inningsPitched", 0))
                out[str(p["id"])] = rec
        (self.cache_dir / "people.json").write_text(json.dumps(out))


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------

def _load(cache_dir: Path, name: str) -> Any:
    p = Path(cache_dir) / name
    return json.loads(p.read_text()) if p.exists() else None


class Record:
    """One player's seasons, by (season, level)."""

    def __init__(self, pid: int, name: str):
        self.id, self.name = pid, name
        self.hit: dict[tuple[int, str], dict[str, float]] = {}
        self.pit: dict[tuple[int, str], dict[str, float]] = {}
        self.age: dict[int, float] = {}
        self.last_team: tuple[int, int] | None = None   # (season, team id)
        self.mlb_pos: str = ""


def load_records(cache_dir: Path, seasons: list[int]) -> dict[int, Record]:
    recs: dict[int, Record] = {}
    for group in ("hitting", "pitching"):
        for sport, level in LEVELS.items():
            for season in seasons:
                rows = _load(cache_dir, f"{group}_{sport}_{season}.json") or []
                by_player: dict[int, list[dict]] = defaultdict(list)
                for r in rows:
                    by_player[r["id"]].append(r)
                for pid, splits in by_player.items():
                    # A traded player may have per-team rows plus a combined row.
                    combined = [s for s in splits if s["team"] is None]
                    use = combined or splits
                    rec = recs.setdefault(pid, Record(pid, splits[0]["name"]))
                    agg: dict[str, float] = defaultdict(float)
                    for s in use:
                        st = s["stat"]
                        if group == "hitting":
                            for k, src in HIT_KEYS.items():
                                agg[k] += float(st.get(src, 0) or 0)
                        else:
                            agg["IP"] += _ip(st.get("inningsPitched", 0))
                            for k, src in PIT_KEYS.items():
                                agg[k] += float(st.get(src, 0) or 0)
                        if st.get("age"):
                            rec.age[season] = float(st["age"])
                    target = rec.hit if group == "hitting" else rec.pit
                    key = (season, level)
                    target[key] = {k: target.get(key, {}).get(k, 0) + v for k, v in agg.items()}
                    team = next((s["team"] for s in splits if s["team"]), None)
                    if team and (rec.last_team is None or season >= rec.last_team[0]):
                        rec.last_team = (season, team)
                    if sport == 1 and splits[0].get("pos"):
                        rec.mlb_pos = splits[0]["pos"]
    return recs


def _age_mult(age: float) -> float:
    return 1 + 0.006 * (29 - age) if age < 29 else 1 - 0.003 * (age - 29)


def _league_rates(recs: dict[int, Record], seasons: list[int], hit: bool) -> dict[str, float]:
    tot: dict[str, float] = defaultdict(float)
    for r in recs.values():
        for (season, level), line in (r.hit if hit else r.pit).items():
            if level == "MLB" and season in seasons:
                for k, v in line.items():
                    tot[k] += v
    denom = tot["PA" if hit else "IP"] or 1.0
    return {k: v / denom for k, v in tot.items()}


def _level_of(r: Record, season: int, hit: bool) -> str | None:
    lines = {lv: l for (s, lv), l in (r.hit if hit else r.pit).items() if s == season}
    if not lines:
        return None
    key = "PA" if hit else "IP"
    # Highest level with real playing time.
    for lv in ("MLB", "AAA", "AA", "A+", "A"):
        if lines.get(lv, {}).get(key, 0) >= (50 if hit else 15):
            return lv
    return max(lines, key=lambda lv: lines[lv].get(key, 0))


def project_hitter(r: Record, seasons: list[int], lg: dict[str, float], target: int) -> dict | None:
    wsum: dict[str, float] = defaultdict(float)
    wpa = mlb_wpa = 0.0
    for w, season in zip(HIT_WEIGHTS, seasons):
        for lv in LEVELS.values():
            line = r.hit.get((season, lv))
            if not line or not line.get("PA"):
                continue
            f = HIT_FACTOR[lv]
            ww = w * (1.0 if lv == "MLB" else MILB_WEIGHT)
            for k in HIT_KEYS:
                if k == "PA":
                    continue
                v = line.get(k, 0)
                if lv != "MLB" and k in HIT_GOOD:
                    if k == "HR":
                        v *= f ** 1.5        # power translates worst
                    elif k == "SB":
                        v *= 0.85            # speed translates well
                    else:
                        v *= f
                wsum[k] += ww * v
            wpa += ww * line["PA"]
            if lv == "MLB":
                mlb_wpa += ww * line["PA"]
    if wpa < 30:
        return None
    mlb_share = mlb_wpa / wpa
    base = mlb_share + (1 - mlb_share) * 0.88   # unproven players regress to below average
    rates = {}
    for k in HIT_KEYS:
        if k == "PA":
            continue
        lg_k = lg.get(k, 0) * (base if k in HIT_GOOD else 1.0)
        rates[k] = (wsum[k] + REG_PA * lg_k) / (wpa + REG_PA)
    age = _proj_age(r, target)
    m = _age_mult(age)
    for k in HIT_GOOD:
        rates[k] *= m

    pa1 = r.hit.get((seasons[0], "MLB"), {}).get("PA", 0)
    pa2 = r.hit.get((seasons[1], "MLB"), {}).get("PA", 0) if len(seasons) > 1 else 0
    had_mlb = any(r.hit.get((s, "MLB"), {}).get("PA", 0) for s in seasons)
    level = _level_of(r, seasons[0], True) or _level_of(r, seasons[1], True) or "A"
    extra = 200 if had_mlb else {"AAA": 150, "AA": 50}.get(level, 0)
    pa = 0.5 * pa1 + 0.1 * pa2 + extra
    return {"rates": rates, "pa": pa, "age": age, "level": level, "mlb_share": mlb_share}


def project_pitcher(r: Record, seasons: list[int], lg: dict[str, float], target: int) -> dict | None:
    wsum: dict[str, float] = defaultdict(float)
    wip = mlb_wip = 0.0
    gs = g = 0.0
    for w, season in zip(PIT_WEIGHTS, seasons):
        for lv in LEVELS.values():
            line = r.pit.get((season, lv))
            if not line or not line.get("IP"):
                continue
            ww = w * (1.0 if lv == "MLB" else MILB_WEIGHT)
            for k in PIT_KEYS:
                v = line.get(k, 0)
                if k in PIT_BAD_KEYS:
                    v *= PIT_BAD[lv]
                elif k == "K":
                    v *= PIT_K[lv]
                elif k in ("SV", "HLD") and lv != "MLB":
                    v = 0.0          # minor-league saves say little about MLB role
                wsum[k] += ww * v
            wip += ww * line["IP"]
            gs += w * line.get("GS", 0)
            g += w * line.get("G", 0)
            if lv == "MLB":
                mlb_wip += ww * line["IP"]
    if wip < 10:
        return None
    mlb_share = mlb_wip / wip
    base = mlb_share + (1 - mlb_share) * 1.10    # unproven pitchers regress to worse
    rates = {}
    for k in PIT_KEYS:
        if k in ("ER", "BBA", "HA"):
            rates[k] = (wsum[k] + REG_IP * lg.get(k, 0) * base) / (wip + REG_IP)
        elif k == "K":
            rates[k] = (wsum[k] + REG_IP * lg.get(k, 0) / base) / (wip + REG_IP)
        else:  # role stats: no regression
            rates[k] = wsum[k] / wip
    age = _proj_age(r, target)
    m = _age_mult(age)
    for k in PIT_BAD_KEYS:
        rates[k] /= m
    rates["K"] *= m

    starter = g > 0 and gs / g >= 0.5
    ip1 = r.pit.get((seasons[0], "MLB"), {}).get("IP", 0)
    ip2 = r.pit.get((seasons[1], "MLB"), {}).get("IP", 0) if len(seasons) > 1 else 0
    had_mlb = any(r.pit.get((s, "MLB"), {}).get("IP", 0) for s in seasons)
    level = _level_of(r, seasons[0], False) or _level_of(r, seasons[1], False) or "A"
    extra = (60 if starter else 25) if had_mlb else ({"AAA": 40, "AA": 15}.get(level, 0) if starter
                                                      else {"AAA": 15}.get(level, 0))
    ip = 0.5 * ip1 + 0.1 * ip2 + extra
    return {"rates": rates, "ip": ip, "age": age, "level": level, "starter": starter,
            "mlb_share": mlb_share}


def _proj_age(r: Record, target: int) -> float:
    if not r.age:
        return 27.0
    season = max(r.age)
    return r.age[season] + (target - season)


def hitter_line(rates: dict[str, float], pa: float) -> dict[str, float]:
    line = {k: v * pa for k, v in rates.items()}
    line["PA"] = pa
    return line


def pitcher_line(rates: dict[str, float], ip: float) -> dict[str, float]:
    line = {k: v * ip for k, v in rates.items() if k != "G"}
    line["IP"] = ip
    return line


def build_players(cache_dir: Path, target_season: int, n_seasons: int = 3) -> dict[int, Player]:
    """MLBAM id -> Player with a projection (and a prospect line when relevant)."""
    seasons = [target_season - 1 - i for i in range(n_seasons)]
    recs = load_records(cache_dir, seasons)
    if not recs:
        return {}
    teams = {int(k): v for k, v in (_load(cache_dir, "teams.json") or {}).items()}
    people = _load(cache_dir, "people.json") or {}
    lg_hit = _league_rates(recs, seasons, True)
    lg_pit = _league_rates(recs, seasons, False)

    def org(r: Record) -> str:
        if not r.last_team:
            return ""
        t = teams.get(r.last_team[1], {})
        parent = teams.get(t.get("parent") or 0, {})
        return canon_abbr(parent.get("abbr") or t.get("abbr", ""))

    out: dict[int, Player] = {}
    for r in recs.values():
        hit_pa = sum(l.get("PA", 0) for l in r.hit.values())
        pit_ip = sum(l.get("IP", 0) for l in r.pit.values())
        is_pitcher = pit_ip * 4 > hit_pa or r.mlb_pos == "P"
        proj = (project_pitcher(r, seasons, lg_pit, target_season) if is_pitcher
                else project_hitter(r, seasons, lg_hit, target_season))
        if proj is None:
            continue
        p = Player(id=f"mlb:{r.id}", name=r.name, mlb_team=org(r), age=proj["age"])
        p.mlbam_id = r.id
        p.level = proj["level"] if proj["level"] != "MLB" else ""
        if is_pitcher:
            p.positions = ["SP" if proj["starter"] else "RP"]
            p.proj = pitcher_line(proj["rates"], proj["ip"]) if proj["ip"] > 0 else {}
            full_ip = 150 if proj["starter"] else 65
            full = pitcher_line(proj["rates"], full_ip)
        else:
            p.proj = hitter_line(proj["rates"], proj["pa"]) if proj["pa"] > 0 else {}
            full = hitter_line(proj["rates"], 600)
        if proj["mlb_share"] < 0.5 and proj["age"] <= PROSPECT_MAX_AGE:
            # A cup of coffee in the majors doesn't make him a sure thing.
            lv = "AAA" if proj["level"] == "MLB" else proj["level"]
            p.prospect_proj = full
            p.eta = target_season + PROSPECT_ETA_OFFSET.get(lv, 3)
            rel_age = proj["age"] - TYPICAL_AGE.get(lv, 23)
            p.hit_rate = min(0.9, PROSPECT_HIT_RATE.get(lv, 0.3) * min(2.0, max(0.3, 1 - 0.18 * rel_age)))
        if str(r.id) in people:
            p.career_ab = people[str(r.id)]["ab"]
            p.career_ip = people[str(r.id)]["ip"]
        out[r.id] = p
    return out


# --------------------------------------------------------------------------
# Matching to Fantrax
# --------------------------------------------------------------------------

def match_to_fantrax(fantrax_players: dict[str, Player], mlb_players: dict[int, Player]) -> dict[str, int]:
    """Fantrax id -> MLBAM id, by name plus organisation."""
    idx: dict[str, list[Player]] = defaultdict(list)
    for p in mlb_players.values():
        idx[norm_name(p.name)].append(p)
    out = {}
    for fid, fp in fantrax_players.items():
        cands = idx.get(norm_name(fp.name))
        if not cands:
            continue
        team = canon_abbr(fp.mlb_team)
        same_org = [c for c in cands if c.mlb_team and c.mlb_team == team]
        if len(same_org) == 1:
            out[fid] = same_org[0].mlbam_id
        elif len(cands) == 1 and (not team or team in ("(N/A)", "FA") or not cands[0].mlb_team):
            out[fid] = cands[0].mlbam_id
        elif len(cands) == 1 and len(same_org) == 0:
            # Unique name but orgs disagree (trade/signing since): accept if the
            # hitter/pitcher side agrees.
            if fp.positions and fp.is_pitcher == cands[0].is_pitcher:
                out[fid] = cands[0].mlbam_id
    return out
