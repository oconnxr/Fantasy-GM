"""Projection and prospect imports.

The GM values players from projection CSVs you download yourself -- for
example FanGraphs' Steamer or ZiPS exports (Projections -> Export Data) --
plus an optional prospect list (e.g. a FanGraphs The Board export) for
players without an MLB projection. Column names are matched loosely so
exports from other sites work as long as the usual headers are present.

Files, all optional, read from ``data/``:
  batters.csv    PA, AB, H, R, HR, RBI, SB, BB, HBP, SF (AVG / OBP accepted
                 in place of H / walks)
  pitchers.csv   IP, ER or ERA, BB, H, W, SV, HLD, SO/K, GS, WHIP
  prospects.csv  Name, Org/Team, Pos, Age, FV, ETA, Level, Rank
  career.csv     Name, Team, AB, IP  (MLB career totals for minors eligibility)
"""

from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path

from .models import Player

DATA_DIR = Path("data")


def norm_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().replace(".", " ")
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return " ".join(s.split())


def _num(row: dict, *keys: str, default: float = 0.0) -> float:
    for k in keys:
        for rk, v in row.items():
            if rk and rk.strip().lower() == k.lower() and v not in (None, ""):
                try:
                    return float(str(v).replace("%", "").replace(",", ""))
                except ValueError:
                    continue
    return default


def _str(row: dict, *keys: str) -> str:
    for k in keys:
        for rk, v in row.items():
            if rk and rk.strip().lower() == k.lower() and v:
                return str(v).strip()
    return ""


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _pid(row: dict, prefix: str) -> str:
    pid = _str(row, "PlayerId", "playerid", "MLBAMID", "xMLBAMID", "id")
    return f"{prefix}:{pid}" if pid else f"{prefix}:{norm_name(_str(row, 'Name', 'PlayerName'))}"


def load_batters(path: Path) -> list[Player]:
    out = []
    for row in _read(path):
        name = _str(row, "Name", "PlayerName")
        if not name:
            continue
        ab = _num(row, "AB")
        h = _num(row, "H")
        if not h and ab:
            h = _num(row, "AVG") * ab
        pa = _num(row, "PA")
        bb, hbp, sf = _num(row, "BB"), _num(row, "HBP"), _num(row, "SF")
        if not (bb or hbp) and pa and _num(row, "OBP"):
            bb = max(0.0, _num(row, "OBP") * (ab + sf + (pa - ab - sf)) - h)
        out.append(Player(
            id=_pid(row, "fg"),
            name=name,
            mlb_team=_str(row, "Team"),
            positions=[p for p in re.split(r"[/,]", _str(row, "POS", "Pos", "Position")) if p] ,
            age=_num(row, "Age", default=0) or None,
            proj={"PA": pa, "AB": ab, "H": h, "R": _num(row, "R"), "HR": _num(row, "HR"),
                  "RBI": _num(row, "RBI"), "SB": _num(row, "SB"), "BB": bb, "HBP": hbp, "SF": sf},
        ))
    return out


def load_pitchers(path: Path) -> list[Player]:
    out = []
    for row in _read(path):
        name = _str(row, "Name", "PlayerName")
        if not name:
            continue
        ip = _num(row, "IP")
        er = _num(row, "ER") or _num(row, "ERA") * ip / 9
        bb = _num(row, "BB")
        ha = _num(row, "H")
        if not (bb or ha) and ip:
            ha = _num(row, "WHIP") * ip  # split unknown; only the sum matters
        gs = _num(row, "GS")
        g = _num(row, "G")
        pos = _str(row, "POS", "Pos")
        if not pos:
            pos = "SP" if g and gs / g >= 0.5 or (not g and gs >= 5) else "RP"
        out.append(Player(
            id=_pid(row, "fg"),
            name=name,
            mlb_team=_str(row, "Team"),
            positions=[pos.upper()],
            age=_num(row, "Age", default=0) or None,
            proj={"IP": ip, "ER": er, "BBA": bb, "HA": ha, "W": _num(row, "W"),
                  "SV": _num(row, "SV"), "HLD": _num(row, "HLD"),
                  "K": _num(row, "SO", "K"), "GS": gs},
        ))
    return out


def _prospect_positions(pos: str) -> list[str]:
    """Prospect lists write pitchers as RHP/LHP; the GM uses SP/RP."""
    out = []
    for p in re.split(r"[/,]", pos.upper()) if pos else []:
        p = p.strip()
        if p in ("RHP", "LHP", "HP"):
            p = "SP"
        elif p in ("RHRP", "LHRP"):
            p = "RP"
        if p and p not in out:
            out.append(p)
    return out


def load_prospects(path: Path) -> list[Player]:
    out = []
    for row in _read(path):
        name = _str(row, "Name", "PlayerName")
        if not name:
            continue
        fv_raw = _str(row, "FV")
        fv = float(re.sub(r"[^0-9.]", "", fv_raw)) if re.search(r"\d", fv_raw) else None
        eta = int(_num(row, "ETA", default=0)) or None
        rank = int(_num(row, "Rank", "Top 100", "Ovr Rank", default=0)) or None
        pos = _str(row, "Pos", "POS", "Position")
        out.append(Player(
            id=_pid(row, "pros"),
            name=name,
            mlb_team=_str(row, "Org", "Team"),
            positions=_prospect_positions(pos),
            age=_num(row, "Age", default=0) or None,
            fv=fv, eta=eta, prospect_rank=rank,
            level=_str(row, "Level", "Highest Level", "Lvl"),
        ))
    return out


def load_career(path: Path) -> dict[str, tuple[float, float]]:
    return {norm_name(_str(r, "Name")): (_num(r, "AB"), _num(r, "IP")) for r in _read(path)}


def load_all(data_dir: Path = DATA_DIR) -> dict[str, list[Player] | dict]:
    d = Path(data_dir)
    return {
        "batters": load_batters(d / "batters.csv"),
        "pitchers": load_pitchers(d / "pitchers.csv"),
        "prospects": load_prospects(d / "prospects.csv"),
        "career": load_career(d / "career.csv"),
    }
