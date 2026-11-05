"""Parsers and the end-to-end build. The Fantrax payloads below are shaped
after the fxea/general endpoints but are hand-written test data."""

import json

from fantasy_gm import fantrax, projections
from fantasy_gm.config import LeagueConfig
from fantasy_gm.league import build_league
from fantasy_gm.models import MINORS, RESERVE

LEAGUE_INFO = {
    "teamInfo": {"t1": {"name": "Sluggers", "id": "t1"}, "t2": {"name": "Aces", "id": "t2"}},
    "rosterInfo": {"positionConstraints": {"C": {"maxActive": 1}, "OF": {"maxActive": 3},
                                           "SP": {"maxActive": 5}, "RP": {"maxActive": 2}}},
}
ROSTERS = {"period": 1, "rosters": {
    "t1": {"teamName": "Sluggers", "rosterItems": [
        {"id": "a1", "position": "OF", "status": "ACTIVE"},
        {"id": "a2", "position": "SP", "status": "RESERVE"},
        {"id": "a3", "position": "SS", "status": "MINORS"}]},
    "t2": {"teamName": "Aces", "rosterItems": [{"id": "b1", "position": "C", "status": "ACTIVE"}]},
}}
PICKS = {"futureDraftPicks": [
    {"round": 1, "year": 2027, "currentOwnerTeamId": "t1", "originalOwnerTeamId": "t2"},
    {"round": 2, "year": 2027, "currentOwnerTeamId": "t2", "originalOwnerTeamId": "t2"},
    {"round": "bad"}]}
PLAYERS = {
    "a1": {"fantraxId": "a1", "name": "Doe, John", "team": "NYY", "position": "OF"},
    "a2": {"fantraxId": "a2", "name": "Roe, Rick", "team": "BOS", "position": "SP"},
    "a3": {"fantraxId": "a3", "name": "Young, Kid", "team": "SEA", "position": "SS,2B"},
    "b1": {"fantraxId": "b1", "name": "Backstop, Bo", "team": "CHC", "position": "C"},
}


def test_parsers():
    players = fantrax.parse_players(PLAYERS)
    assert players["a1"].name == "John Doe"
    assert players["a3"].positions == ["SS", "2B"]
    teams = fantrax.parse_teams(LEAGUE_INFO, ROSTERS)
    assert teams["t1"].name == "Sluggers"
    statuses = {r.player_id: r.status for r in teams["t1"].roster}
    assert statuses == {"a1": "ACTIVE", "a2": RESERVE, "a3": MINORS}
    picks = fantrax.parse_draft_picks(PICKS)
    assert len(picks) == 2 and picks[0].original_team_id == "t2" and picks[0].owner_team_id == "t1"
    assert fantrax.parse_roster_slots(LEAGUE_INFO) == {"C": 1, "OF": 3, "SP": 5, "RP": 2}


def test_parsers_tolerate_missing_data():
    assert fantrax.parse_players(None) == {}
    assert fantrax.parse_teams(None, None) == {}
    assert fantrax.parse_draft_picks({}) == []
    assert fantrax.parse_roster_slots({}) is None


def test_norm_name():
    assert projections.norm_name("Ronald Acuña Jr.") == "ronald acuna"
    assert projections.norm_name("J.D. Martinez") == "j d martinez"


def _write(path, header, rows):
    path.write_text("\n".join([",".join(header)] + [",".join(map(str, r)) for r in rows]) + "\n")


def test_build_league_end_to_end(tmp_path):
    fx = tmp_path / "fantrax"
    fx.mkdir()
    for name, data in (("league_info", LEAGUE_INFO), ("rosters", ROSTERS),
                       ("draft_picks", PICKS), ("players", PLAYERS)):
        (fx / f"{name}.json").write_text(json.dumps(data))
    _write(tmp_path / "batters.csv", ["Name", "Team", "Age", "PA", "AB", "H", "HR", "RBI", "SB", "PlayerId"],
           [["John Doe", "NYY", 28, 600, 540, 150, 30, 90, 10, "1"],
            ["Bo Backstop", "CHC", 30, 450, 400, 100, 15, 60, 1, "2"],
            ["Free Agent", "MIA", 25, 500, 450, 110, 12, 50, 20, "3"]])
    _write(tmp_path / "pitchers.csv", ["Name", "Team", "Age", "IP", "ERA", "WHIP", "W", "SV", "HLD", "SO", "GS", "G"],
           [["Rick Roe", "BOS", 29, 180, 3.50, 1.15, 12, 0, 0, 190, 30, 30],
            ["Closer Guy", "TB", 31, 65, 2.90, 1.05, 4, 32, 2, 80, 0, 65]])
    _write(tmp_path / "prospects.csv", ["Name", "Org", "Pos", "Age", "FV", "ETA", "Level"],
           [["Kid Young", "SEA", "SS", 19, "60", 2028, "A+"]])

    cfg = LeagueConfig()
    cfg.teams = 2
    league = build_league(cfg, tmp_path)
    assert league.cfg.slots["SP"] == 5  # pulled from league info
    doe = league.find_player("john doe")
    assert doe.id == "a1" and doe.proj["HR"] == 30 and doe.age == 28
    roe = league.players["a2"]
    assert abs(roe.stat("ERA") - 3.50) < 1e-6 and abs(roe.stat("WHIP") - 1.15) < 1e-6
    kid = league.players["a3"]
    assert kid.fv == 60 and kid.dynasty_value > 0
    assert "Free Agent" in {p.name for p in league.free_agents()}
    assert league.teams["t1"].picks and league.pick_curve


SETTINGS_INFO = {
    "leagueName": "Test League", "seasonYear": 2027,
    "scoringSystem": {"scoringCategories": {
        "HITTING": {"R": {}, "HR": {}, "OBP": {}}, "PITCHING": {"ERA": {}, "K": {}, "WHIP": {}}}},
    "rosterInfo": {"maxTotalPlayers": 48, "maxTotalActivePlayers": 19, "maxTotalReservePlayers": 14,
                   "positionConstraints": {"INF": {"maxActive": 1}, "UT": {"maxActive": 2}}},
    "scoringPeriods": [
        {"number": 1, "startDate": "2027-03-24T12:00:00.0-0400", "endDate": "2027-03-29T03:59:59.0-0400"},
        {"number": 2, "startDate": "2027-03-29T04:00:00.0-0400", "endDate": "2027-04-05T03:59:59.0-0400"}],
    "matchups": [
        {"period": 1, "matchupList": [{"home": {"id": "a"}, "away": {"id": "b"}}]},
        {"period": 2, "matchupList": [{"home": {"id": "c"}, "away": {"id": "a"}}]}],
}


def test_parse_settings():
    s = fantrax.parse_settings(SETTINGS_INFO)
    assert s["hitting_cats"] == ["R", "HR", "OBP"]
    assert s["pitching_cats"] == ["ERA", "K", "WHIP"]
    assert s["inverse_cats"] == {"ERA", "WHIP"}
    assert s["bench"] == 14 and s["max_roster"] == 48
    assert s["slots"] == {"INF": 1, "UT": 2}
    assert s["season"] == 2027 and s["name"] == "Test League"


def test_current_period_and_opponent():
    from datetime import date
    assert fantrax.current_period(SETTINGS_INFO, date(2026, 11, 5)) == 1   # offseason: next up
    assert fantrax.current_period(SETTINGS_INFO, date(2027, 4, 1)) == 2
    assert fantrax.current_period(SETTINGS_INFO, date(2027, 10, 1)) is None
    assert fantrax.opponent(SETTINGS_INFO, "a", 1) == "b"
    assert fantrax.opponent(SETTINGS_INFO, "a", 2) == "c"
    assert fantrax.opponent(SETTINGS_INFO, "z", 2) is None


def test_current_draft_picks_get_season_year():
    picks = fantrax.parse_draft_picks({"currentDraftPicks": [{"round": 1, "pick": 8, "teamId": "t1"}]}, 2027)
    assert picks[0].year == 2027 and picks[0].pick_number == 8 and picks[0].owner_team_id == "t1"


def test_obp_valued_from_walks(league, cfg):
    import copy
    from fantasy_gm.valuation import value_players
    players = [copy.deepcopy(p) for p in league.players.values()]
    base = next(p for p in players if p.id == "h1")
    walker = copy.deepcopy(base)
    walker.id, walker.proj["BB"] = "walker", base.proj["BB"] + 40
    players.append(walker)
    value_players(players, cfg)
    base = next(p for p in players if p.id == "h1")
    assert walker.stat("OBP") > base.stat("OBP")
    assert walker.cat_values["OBP"] > base.cat_values["OBP"]
    assert walker.cat_values["HR"] == base.cat_values["HR"]
