from fantasy_gm import lineup, prospects, waivers
from fantasy_gm.lineup import WeekContext, assign, optimize


def test_lineup_is_legal_and_full(league, cfg):
    res = optimize(league, "T1", "T2")
    slots = lineup._slot_list(cfg)
    starters = [p for ps in res.starters.values() for p in ps]
    assert len(starters) <= len(slots)
    assert assign(starters, slots) is not None
    for slot, ps in res.starters.items():
        for p in ps:
            assert lineup._eligible(p, slot)
    assert set(res.probs) == set(cfg.categories)
    assert 0 <= res.expected_wins <= len(cfg.categories)


def test_optimizer_beats_or_matches_value_lineup(league, cfg):
    slots = lineup._slot_list(cfg)
    pool = lineup.available(league, "T1")
    naive = lineup._best_value_lineup(pool, slots)
    ctx = WeekContext()
    opp = lineup._best_value_lineup(lineup.available(league, "T2"), slots)
    opp_agg = lineup._sum([lineup.weekly_line(p, ctx) for p in opp])
    naive_wins = sum(lineup.win_probs(lineup._sum([lineup.weekly_line(p, ctx) for p in naive]),
                                      opp_agg, cfg).values())
    assert optimize(league, "T1", "T2", ctx).expected_wins >= naive_wins - 1e-9


def test_two_start_pitcher_doubles_weekly_line(league):
    sp = next(p for p in league.players.values() if "SP" in p.positions and p.proj.get("GS"))
    one = lineup.weekly_line(sp, WeekContext())
    two = lineup.weekly_line(sp, WeekContext(starts={sp.id: 2}))
    assert abs(two["K"] - 2 * one["K"]) < 1e-9


def test_catcher_slot_only_takes_catchers(league):
    hitters = [p for p in lineup.available(league, "T1") if p.is_hitter and "C" not in p.positions]
    assert assign(hitters[:1], ["C"]) is None


def test_waiver_targets_improve_on_drop(league):
    # Free up some useful free agents by releasing T4's reserves.
    league.teams["T4"].roster = [r for r in league.teams["T4"].roster if r.status != "RESERVE"]
    ideas = waivers.season_targets(league, "T1", limit=5)
    assert ideas
    for i in ideas:
        assert i.score > 0
        assert league.owner_of(i.add.id) is None
        assert i.drop is None or league.owner_of(i.drop.id) == "T1"


def test_week_waivers_gain_is_positive(league):
    league.teams["T4"].roster = [r for r in league.teams["T4"].roster if r.status != "RESERVE"]
    base, ideas = waivers.week_targets(league, "T1", "T2", limit=3, shortlist=10)
    assert 0 < base < len(league.cfg.categories)
    assert all(g > 0 for _, _, g in ideas)


def test_minors_eligibility(league, cfg):
    p = league.players["m1"]
    p.career_ab = 0
    assert prospects.eligibility(p, cfg)[0] == "eligible"
    p.career_ab = cfg.max_career_ab * 0.9
    assert prospects.eligibility(p, cfg)[0] == "warning"
    p.career_ab = cfg.max_career_ab + 1
    assert prospects.eligibility(p, cfg)[0] == "lost"


def test_team_report_flags_lost_eligibility(league, cfg):
    mine = [r.player_id for r in league.teams["T1"].roster if r.status == "MINORS"]
    hitter = next(league.players[i] for i in mine if league.players[i].is_hitter)
    hitter.career_ab = cfg.max_career_ab + 50
    report = {r.player.id: r for r in prospects.team_report(league, "T1")}
    assert report[hitter.id].action == "ACTIVATE OR CUT"
    assert all(r.action in {"ACTIVATE OR CUT", "PROMOTE", "CUT", "WATCH", "STASH"} for r in report.values())


def test_minor_free_agents_are_unrostered(league):
    for p, v in prospects.minor_free_agents(league):
        assert league.owner_of(p.id) is None
