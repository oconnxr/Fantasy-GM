from fantasy_gm import trade
from fantasy_gm.models import MINORS


def _best(league, tid):
    mode = league.teams[tid].mode
    return max(league.team_players(tid), key=lambda p: league.dyn(p, mode))


def _worst_mlb(league, tid):
    return min((p for p in league.team_players(tid) if p.has_projection), key=lambda p: league.dyn(p))


def test_lopsided_trade_grades(league):
    star = _best(league, "T2")
    scrub = _worst_mlb(league, "T1")
    r = trade.analyze(league, trade.Side("T1", [scrub]), trade.Side("T2", [star]))
    assert r.my_net > 0 and r.their_net < 0
    assert r.grade in ("A+", "A")
    reverse = trade.analyze(league, trade.Side("T1", [_best(league, "T1")]),
                            trade.Side("T2", [_worst_mlb(league, "T2")]))
    assert reverse.grade == "F"


def test_losing_trade_gets_counters(league):
    mode = league.teams["T1"].mode
    mid = sorted((p for p in league.team_players("T1") if league.dyn(p, mode) > 0),
                 key=lambda p: league.dyn(p, mode))[0]
    r = trade.analyze(league, trade.Side("T1", [mid]), trade.Side("T2", [_worst_mlb(league, "T2")]))
    assert r.my_net < 0
    assert r.counters, "a losing trade should come with counter-offers"
    assert all(mine >= 0 for _, mine, _ in r.counters)
    # Sorted by what the other side is most likely to accept.
    theirs = [t for _, _, t in r.counters]
    assert theirs == sorted(theirs, reverse=True)


def test_two_for_one_charges_for_cuts(league):
    # Fill T2's roster so receiving an extra MLB player forces a cut.
    t1_players = [p for p in league.team_players("T1") if p.has_projection]
    give = sorted(t1_players, key=lambda p: league.dyn(p))[-3:-1]
    get = [_worst_mlb(league, "T2")]
    r = trade.analyze(league, trade.Side("T1", give), trade.Side("T2", get))
    # T1 sends two, receives one: gains an open spot (credit, no cuts).
    assert r.my_roster_adj >= 0 and not r.my_cuts
    # T2 receives two, sends one: roster is full, so it must cut someone.
    assert len(r.their_cuts) == 1
    assert r.their_roster_adj == -league.dyn(r.their_cuts[0], league.teams["T2"].mode)


def test_category_delta_reflects_players(league):
    give = [p for p in league.team_players("T1") if p.is_pitcher and p.has_projection][:1]
    get = [p for p in league.team_players("T2") if p.is_hitter and p.has_projection]
    get = [max(get, key=lambda p: p.proj["HR"])]
    r = trade.analyze(league, trade.Side("T1", give), trade.Side("T2", get))
    assert r.cat_delta["K"] <= 0
    assert r.cat_delta["HR"] >= 0


def test_picks_count_in_trades(league):
    pick = league.teams["T2"].picks[0]
    r = trade.analyze(league, trade.Side("T1", []), trade.Side("T2", [], [pick]))
    assert r.my_get > 0 and r.my_net > 0


def test_find_trades_returns_mutual_wins(league):
    league.teams["T1"].mode = "contending"
    league.teams["T3"].mode = "rebuilding"
    for _, give, get, tid, mine, theirs in trade.find_trades(league, "T1"):
        assert mine > 0 and theirs > 0
        assert league.owner_of(give.id) == "T1" and league.owner_of(get.id) == tid


def test_minors_players_count_toward_roster_limit(league):
    prospect = next(p for p in league.team_players("T2") if not p.has_projection)
    r = trade.analyze(league, trade.Side("T1", []), trade.Side("T2", [prospect]))
    assert len(r.my_cuts) == 1 and r.my_cuts[0].id != prospect.id


def test_offseason_overage_only_charges_extra_cuts(league):
    # T1 is 3 over the limit already; a 1-for-1 shouldn't charge for those cuts.
    league.cfg.max_roster = 27
    give = max((p for p in league.team_players("T1") if p.has_projection), key=lambda p: league.dyn(p))
    get = max((p for p in league.team_players("T2") if p.has_projection), key=lambda p: league.dyn(p))
    r = trade.analyze(league, trade.Side("T1", [give]), trade.Side("T2", [get]))
    assert not r.my_cuts and not r.their_cuts
    assert r.my_roster_adj == 0 and r.their_roster_adj == 0


def test_ir_excluded_from_roster_limit(league):
    from fantasy_gm.models import INJURED
    n = len(league.counted_roster("T1"))
    league.teams["T1"].roster[0].status = INJURED
    assert len(league.counted_roster("T1")) == n - 1
    league.cfg.ir_counts_toward_max = True
    assert len(league.counted_roster("T1")) == n
