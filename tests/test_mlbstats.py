"""MLB-stats projection model, on hand-written stat lines."""

import json

from fantasy_gm import mlbstats
from fantasy_gm.models import Player


def test_innings_parse():
    assert mlbstats._ip("123.1") == 123 + 1 / 3
    assert mlbstats._ip("45.2") == 45 + 2 / 3
    assert mlbstats._ip(0) == 0


def test_abbr_aliases():
    assert mlbstats.canon_abbr("CHW") == mlbstats.canon_abbr("CWS")
    assert mlbstats.canon_abbr("ARI") == "AZ"


def _hit(pa, hr, team=1, age=27):
    ab = pa * 0.88
    return {"plateAppearances": pa, "atBats": ab, "hits": ab * 0.26, "runs": pa * 0.12,
            "homeRuns": hr, "rbi": pa * 0.12, "stolenBases": 5, "baseOnBalls": pa * 0.08,
            "hitByPitch": 5, "sacFlies": 4, "age": age}


def _write(tmp, group, sport, season, rows):
    (tmp / f"{group}_{sport}_{season}.json").write_text(json.dumps(rows))


def _league(tmp, season, extra_mlb=(), extra=()):
    # A league-average population so regression has a target.
    rows = [{"id": 1000 + i, "name": f"Avg {i}", "team": 1, "pos": "1B", "stat": _hit(600, 20)}
            for i in range(30)]
    _write(tmp, "hitting", 1, season, rows + list(extra_mlb))
    for sport, level_rows in extra:
        _write(tmp, "hitting", sport, season, level_rows)
    (tmp / "teams.json").write_text(json.dumps({"1": {"abbr": "NYY", "parent": None, "sport": 1},
                                                "2": {"abbr": "SWB", "parent": 1, "sport": 11}}))


def test_marcel_regresses_and_weights(tmp_path):
    slugger = {"id": 1, "name": "Big Bopper", "team": 1, "pos": "RF", "stat": _hit(600, 50)}
    _league(tmp_path, 2026, extra_mlb=[slugger])
    players = mlbstats.build_players(tmp_path, 2027, n_seasons=1)
    p = players[1]
    hr_rate = p.proj["HR"] / p.proj["PA"]
    assert 20 / 600 < hr_rate < 50 / 600            # regressed toward the league
    assert p.mlb_team == "NYY" and not p.prospect_proj
    assert abs(p.proj["PA"] - (0.5 * 600 + 200)) < 1e-6


def test_minor_league_stats_are_discounted(tmp_path):
    aaa = {"id": 2, "name": "Farm Hand", "team": 2, "pos": "SS", "stat": _hit(500, 25, team=2, age=22)}
    mlb_twin = {"id": 3, "name": "Big Leaguer", "team": 1, "pos": "SS", "stat": _hit(500, 25, age=22)}
    _league(tmp_path, 2026, extra_mlb=[mlb_twin], extra=[(11, [aaa])])
    players = mlbstats.build_players(tmp_path, 2027, n_seasons=1)
    farm, big = players[2], players[3]
    assert farm.mlb_team == "NYY"                   # minor-league team maps to parent org
    assert farm.prospect_proj and farm.hit_rate and 0 < farm.hit_rate <= 0.9
    assert farm.eta == 2027
    farm_hr = farm.prospect_proj["HR"] / farm.prospect_proj["PA"]
    big_hr = big.proj["HR"] / big.proj["PA"]
    assert farm_hr < big_hr


def test_match_to_fantrax_uses_org():
    mlb = {
        10: Player(id="mlb:10", name="Will Smith", mlb_team="LAD", positions=["C"]),
        11: Player(id="mlb:11", name="Will Smith", mlb_team="TEX", positions=["RP"]),
        12: Player(id="mlb:12", name="Unique Guy", mlb_team="CWS", positions=["SS"]),
    }
    for k, p in mlb.items():
        p.mlbam_id = k
    fx = {
        "a": Player(id="a", name="Will Smith", mlb_team="LAD", positions=["C"]),
        "b": Player(id="b", name="Smith, Will", mlb_team="TEX", positions=["RP"]),
        "c": Player(id="c", name="Unique Guy", mlb_team="CHW", positions=["SS"]),
    }
    fx["b"].name = "Will Smith"
    assert mlbstats.match_to_fantrax(fx, mlb) == {"a": 10, "b": 11, "c": 12}
