"""Trade analyzer and trade finder.

Each side is valued from the receiving team's point of view: a contender
values this season's production and discounts picks and prospects, a
rebuilder the reverse. Trades that change roster counts are charged for the
players the receiving team has to cut (or credited with the free agent it
can add), so a 2-for-1 isn't scored as a straight sum.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import teamstats
from .models import MINORS, DraftPick, Player


@dataclass
class Side:
    team_id: str
    players: list[Player] = field(default_factory=list)
    picks: list[DraftPick] = field(default_factory=list)


@dataclass
class TradeResult:
    my_team: str
    their_team: str
    my_mode: str
    their_mode: str
    my_get: float
    my_give: float
    my_roster_adj: float
    their_get: float
    their_give: float
    their_roster_adj: float
    cat_delta: dict[str, float]
    my_cuts: list[Player]
    their_cuts: list[Player]
    counters: list[tuple[str, float, float]] = field(default_factory=list)

    @property
    def my_net(self) -> float:
        return self.my_get - self.my_give + self.my_roster_adj

    @property
    def their_net(self) -> float:
        return self.their_get - self.their_give + self.their_roster_adj

    @property
    def grade(self) -> str:
        return grade(self.my_net, self.my_get + self.my_give)

    @property
    def verdict(self) -> str:
        if self.my_net > 0 and self.their_net >= 0:
            return "Win-win: accept, and they should too."
        if self.my_net > 0:
            return "Good for you. They lose value by their own needs, so expect pushback."
        if self.their_net > 0 and self.my_net > -0.05 * max(1.0, self.my_get + self.my_give):
            return "Roughly fair; slightly favours them."
        return "Decline or counter."


def grade(net: float, volume: float) -> str:
    pct = net / max(volume, 1.0)
    for cut, letter in ((0.25, "A+"), (0.15, "A"), (0.08, "B+"), (0.03, "B"),
                        (-0.03, "C"), (-0.08, "C-"), (-0.15, "D")):
        if pct >= cut:
            return letter
    return "F"


def _side_value(league, side: Side, mode: str) -> float:
    return (sum(league.dyn(p, mode) for p in side.players)
            + sum(league.pick_val(pk, mode) for pk in side.picks))


def _roster_adjustment(league, team_id: str, incoming: list[Player], outgoing: list[Player],
                       mode: str) -> tuple[float, list[Player]]:
    """Cost of cuts forced by a roster-count change (or credit for open spots).

    Players acquired into a minors slot don't use an active spot, so only
    MLB-projected players count toward the active roster change.
    """
    def counts(p: Player) -> bool:
        return p.has_projection

    delta = sum(map(counts, incoming)) - sum(map(counts, outgoing))
    if delta == 0:
        return 0.0, []
    out_ids = {p.id for p in outgoing}
    team = league.teams[team_id]
    current = [league.players[r.player_id] for r in team.roster
               if r.status != MINORS and r.player_id in league.players
               and r.player_id not in out_ids]
    if delta > 0:
        open_spots = max(0, league.cfg.active_roster_size - len(current))
        n_cut = max(0, delta - open_spots)
        cuts = sorted(current, key=lambda p: league.dyn(p, mode))[:n_cut]
        return -sum(league.dyn(p, mode) for p in cuts), cuts
    # Freed spots get filled from waivers.
    fas = sorted((league.dyn(p, mode) for p in league.free_agents() if p.has_projection),
                 reverse=True)
    return sum(fas[: -delta]), []


def analyze(league, my: Side, their: Side, find_counters: bool = True) -> TradeResult:
    my_mode = league.teams[my.team_id].mode
    their_mode = league.teams[their.team_id].mode

    my_adj, my_cuts = _roster_adjustment(league, my.team_id, their.players, my.players, my_mode)
    their_adj, their_cuts = _roster_adjustment(league, their.team_id, my.players, their.players,
                                               their_mode)
    before = teamstats.team_totals(league, my.team_id)
    after = teamstats.team_totals(league, my.team_id, extra=their.players,
                                  remove={p.id for p in my.players} | {p.id for p in my_cuts})
    cat_delta = {c: after[c] - before[c] for c in league.cfg.categories}

    result = TradeResult(
        my_team=my.team_id, their_team=their.team_id, my_mode=my_mode, their_mode=their_mode,
        my_get=_side_value(league, their, my_mode), my_give=_side_value(league, my, my_mode),
        my_roster_adj=my_adj,
        their_get=_side_value(league, my, their_mode), their_give=_side_value(league, their, their_mode),
        their_roster_adj=their_adj,
        cat_delta=cat_delta, my_cuts=my_cuts, their_cuts=their_cuts,
    )
    if find_counters and result.my_net < 0:
        result.counters = suggest_counters(league, my, their)
    return result


def suggest_counters(league, my: Side, their: Side, limit: int = 3) -> list[tuple[str, float, float]]:
    """Single additions from their roster that make the deal a win for you.

    Ranked by how much the other team still likes the deal, i.e. the
    counter they're most likely to accept.
    """
    taken_players = {p.id for p in their.players}
    taken_picks = {id(pk) for pk in their.picks}
    options = []
    team = league.teams[their.team_id]
    for p in league.team_players(their.team_id):
        if p.id in taken_players:
            continue
        r = analyze(league, my, Side(their.team_id, their.players + [p], their.picks), False)
        if r.my_net >= 0:
            options.append((f"add {p}", r.my_net, r.their_net))
    for pk in team.picks:
        if id(pk) in taken_picks:
            continue
        r = analyze(league, my, Side(their.team_id, their.players, their.picks + [pk]), False)
        if r.my_net >= 0:
            options.append((f"add pick {pk.label}", r.my_net, r.their_net))
    options.sort(key=lambda o: o[2], reverse=True)
    return options[:limit]


def find_trades(league, my_team_id: str, limit: int = 15, min_value: float = 1.0):
    """1-for-1 trades that both sides should accept.

    Value differences come from team mode (a rebuilder's veteran is worth
    more to a contender) and from category fit with your roster.
    """
    my_mode = league.teams[my_team_id].mode
    weights = teamstats.need_weights(league, my_team_id)
    year0 = league.cfg.year0_weight[my_mode]
    mine = [p for p in league.team_players(my_team_id) if league.dyn(p, my_mode) >= min_value]
    ideas = []
    for tid, team in league.teams.items():
        if tid == my_team_id:
            continue
        their_mode = team.mode
        for q in league.team_players(tid):
            q_me = league.dyn(q, my_mode)
            if q_me < min_value:
                continue
            q_fit = year0 * (teamstats.fit_value(q, weights) - q.value) if q.has_projection else 0.0
            q_them = league.dyn(q, their_mode)
            for p in mine:
                my_gain = q_me + q_fit - league.dyn(p, my_mode)
                their_gain = league.dyn(p, their_mode) - q_them
                if my_gain > 0 and their_gain > 0:
                    ideas.append((min(my_gain, their_gain), p, q, tid, my_gain, their_gain))
    ideas.sort(key=lambda x: x[0], reverse=True)
    seen, out = set(), []
    for idea in ideas:
        key = (idea[1].id, idea[2].id)
        if key not in seen:
            seen.add(key)
            out.append(idea)
        if len(out) >= limit:
            break
    return out
