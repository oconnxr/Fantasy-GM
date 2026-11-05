import copy

from fantasy_gm import dynasty
from fantasy_gm.models import DraftPick, Player
from fantasy_gm.valuation import value_players


def test_more_homers_is_more_value(league, cfg):
    players = [copy.deepcopy(p) for p in league.players.values()]
    base = next(p for p in players if p.id == "h1")
    clone = copy.deepcopy(base)
    clone.id, clone.proj["HR"] = "clone", base.proj["HR"] + 15
    players.append(clone)
    value_players(players, cfg)
    base = next(p for p in players if p.id == "h1")
    assert clone.value > base.value
    assert clone.cat_values["HR"] > base.cat_values["HR"]
    assert clone.cat_values["OBP"] == base.cat_values["OBP"]


def test_lower_era_is_more_value(league, cfg):
    players = [copy.deepcopy(p) for p in league.players.values()]
    base = next(p for p in players if p.id == "p0")
    clone = copy.deepcopy(base)
    clone.id, clone.proj["ER"] = "clone", base.proj["ER"] * 0.7
    players.append(clone)
    value_players(players, cfg)
    base = next(p for p in players if p.id == "p0")
    assert clone.cat_values["ERA"] > base.cat_values["ERA"]
    assert clone.cat_values["WHIP"] == base.cat_values["WHIP"]


def test_replacement_level_near_zero(league, cfg):
    n = cfg.teams * cfg.hitter_starters_per_team
    hitters = sorted((p.value for p in league.players.values() if p.is_hitter and p.has_projection),
                     reverse=True)
    assert hitters[0] > 0
    assert abs(hitters[n]) < 1.5  # positional adjustments shift it a little


def _good_hitter(league):
    return max((p for p in league.players.values() if p.is_hitter and p.has_projection),
               key=lambda p: p.value)


def test_young_player_worth_more_in_dynasty(league, cfg):
    young = copy.deepcopy(_good_hitter(league))
    old = copy.deepcopy(_good_hitter(league))
    young.age, old.age = 23, 34
    rv = league.ranked_values
    assert dynasty.dynasty_value(young, cfg, rv) > dynasty.dynasty_value(old, cfg, rv)


def test_contender_prefers_now_rebuilder_prefers_later(league, cfg):
    vet = copy.deepcopy(_good_hitter(league))
    vet.age = 33
    prospect = league.players["m0"]
    rv = league.ranked_values
    vet_ratio = dynasty.dynasty_value(vet, cfg, rv, "contending") / dynasty.dynasty_value(vet, cfg, rv, "rebuilding")
    pros_ratio = (dynasty.dynasty_value(prospect, cfg, rv, "contending")
                  / dynasty.dynasty_value(prospect, cfg, rv, "rebuilding"))
    assert vet_ratio > pros_ratio


def test_prospect_value_rises_with_fv_and_falls_with_eta(league, cfg):
    rv = league.ranked_values
    base = Player(id="x", name="X", positions=["SS"], age=20, fv=55, eta=2028)
    better = copy.deepcopy(base)
    better.fv = 65
    later = copy.deepcopy(base)
    later.eta = 2030
    v = lambda p: dynasty.dynasty_value(p, cfg, rv)
    assert v(better) > v(base) > v(later) > 0


def test_below_replacement_veteran_is_worthless(league, cfg):
    scrub = min((p for p in league.players.values() if p.is_hitter and p.has_projection),
                key=lambda p: p.value)
    scrub = copy.deepcopy(scrub)
    scrub.age = 34
    assert dynasty.dynasty_value(scrub, cfg, league.ranked_values) == 0


def test_pick_values_decline_by_round_and_year(league, cfg):
    t = league.teams["T1"].id
    v = lambda y, r: league.pick_val(DraftPick(y, r, t, t))
    assert v(2027, 1) > v(2027, 2) > 0
    assert v(2027, 1) > v(2028, 1)


def test_unranked_prospects_capped_below_ranked_when_list_loaded(league, cfg):
    stats_only = copy.deepcopy(league.players["m0"])
    stats_only.fv, stats_only.prospect_rank = None, None
    stats_only.prospect_value, stats_only.hit_rate = 50.0, 0.9   # absurdly good stats line
    rv = league.ranked_values
    cfg.has_rankings = False
    uncapped = dynasty.dynasty_value(stats_only, cfg, rv)
    cfg.has_rankings = True
    capped = dynasty.dynasty_value(stats_only, cfg, rv)
    ranked = copy.deepcopy(stats_only)
    ranked.prospect_rank = 20
    assert capped < uncapped
    assert capped < dynasty.dynasty_value(ranked, cfg, rv)
