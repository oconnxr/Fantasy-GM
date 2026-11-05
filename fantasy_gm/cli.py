"""`fgm` command line: the virtual GM."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from . import prospects as prospects_mod
from . import trade as trade_mod
from . import waivers as waivers_mod
from .config import LeagueConfig
from .fantrax import FantraxClient, FantraxError
from .league import League, build_league
from .lineup import WeekContext, optimize
from .models import DraftPick, Player


def _load(ctx) -> League:
    if "league" not in ctx.obj:
        cfg = LeagueConfig.load(ctx.obj["config"])
        ctx.obj["league"] = build_league(cfg, Path(ctx.obj["data"]))
    return ctx.obj["league"]


def _fmt_cat(cat: str, v: float) -> str:
    if cat in ("AVG", "OBP", "SLG", "OPS"):
        return f"{v:.3f}".lstrip("0") if v < 1 else f"{v:.3f}"
    if cat in ("ERA", "WHIP"):
        return f"{v:.2f}"
    return f"{v:.0f}" if abs(v) >= 10 else f"{v:.1f}"


def _fmt_delta(cat: str, v: float) -> str:
    if cat in ("AVG", "OBP", "SLG", "OPS"):
        return f"{v:+.4f}"
    if cat in ("ERA", "WHIP"):
        return f"{v:+.3f}"
    return f"{v:+.1f}"


def _pick_label(league: League, pk: DraftPick, holder: str) -> str:
    slot = f".{pk.pick_number:02d}" if pk.pick_number else ""
    via = ""
    if pk.original_team_id != holder:
        orig = league.teams.get(pk.original_team_id)
        via = f" (via {orig.name if orig else pk.original_team_id})"
    return f"{pk.year} R{pk.round}{slot}{via}"


def _need_league_data(league: League) -> None:
    if not league.teams:
        raise click.ClickException("No league data. Run `fgm sync` (or check data/fantrax/).")


def _my_team_id(league: League, team: str | None) -> str:
    _need_league_data(league)
    if team:
        return league.find_team(team).id
    try:
        return league.my_team.id
    except KeyError as e:
        raise click.ClickException(str(e))


def _resolve_players(league: League, names: str) -> list[Player]:
    out = []
    for n in filter(None, (s.strip() for s in names.split(","))):
        try:
            out.append(league.find_player(n))
        except KeyError as e:
            raise click.ClickException(str(e))
    return out


def _resolve_picks(league: League, team_id: str, specs: tuple[str, ...]) -> list[DraftPick]:
    """Pick specs look like 2027:1 or 2027:1:<original team name>."""
    out = []
    owned = league.teams[team_id].picks
    for spec in specs:
        parts = spec.split(":")
        try:
            year, rnd = int(parts[0]), int(parts[1])
        except (IndexError, ValueError):
            raise click.ClickException(f"Bad pick '{spec}': use YEAR:ROUND[:ORIGINAL_TEAM]")
        cands = [p for p in owned if p.year == year and p.round == rnd and p not in out]
        if len(parts) > 2:
            orig = league.find_team(parts[2]).id
            cands = [p for p in cands if p.original_team_id == orig]
        if not cands:
            raise click.ClickException(f"{league.teams[team_id].name} doesn't own pick {spec}")
        out.append(cands[0])
    return out


@click.group()
@click.option("--config", default="league.toml", show_default=True, help="League settings file.")
@click.option("--data", default="data", show_default=True, help="Data directory.")
@click.pass_context
def main(ctx, config, data):
    """Virtual GM for a Fantrax H2H categories dynasty league."""
    ctx.ensure_object(dict)
    ctx.obj.update(config=config, data=data)


@main.command()
@click.pass_context
def sync(ctx):
    """Pull league settings, rosters, picks and players from Fantrax."""
    cfg = LeagueConfig.load(ctx.obj["config"])
    if not cfg.fantrax_league_id:
        raise click.ClickException("Set fantrax_league_id in league.toml")
    client = FantraxClient(cfg.fantrax_league_id, Path(ctx.obj["data"]) / "fantrax")
    try:
        data = client.sync()
    except FantraxError as e:
        raise click.ClickException(str(e))
    rosters = (data.get("rosters") or {}).get("rosters") or {}
    click.echo(f"Synced {len(rosters)} rosters, {len(data.get('players') or {})} players.")


@main.command()
@click.pass_context
def teams(ctx):
    """List teams with ids, strength and auto-detected mode."""
    league = _load(ctx)
    _need_league_data(league)
    rows = sorted(league.teams.values(), key=lambda t: league.team_strength(t.id), reverse=True)
    for t in rows:
        me = " <- you" if t.id == league.cfg.my_team_id else ""
        click.echo(f"{t.id:<20} {t.name:<30} strength {league.team_strength(t.id):6.1f}  {t.mode}{me}")


@main.command()
@click.argument("team", required=False)
@click.pass_context
def roster(ctx, team):
    """Show a roster with current and dynasty values."""
    league = _load(ctx)
    tid = _my_team_id(league, team)
    t = league.teams[tid]
    click.echo(f"{t.name}  ({t.mode})")
    status = {r.player_id: r.status for r in t.roster}
    for p in sorted(league.team_players(tid), key=lambda p: league.dyn(p, t.mode), reverse=True):
        click.echo(f"  {status[p.id]:<16} {str(p):<42} now {p.value:+6.1f}  dyn {league.dyn(p, t.mode):6.1f}")
    if t.picks:
        click.echo("Picks:")
        for pk in sorted(t.picks, key=lambda p: (p.year, p.round)):
            click.echo(f"  {_pick_label(league, pk, tid):<40} value {league.pick_val(pk, t.mode):5.1f}")


@main.command()
@click.option("--top", default=50, show_default=True)
@click.option("--pos", default=None, help="Filter by position, e.g. SS, SP, RP.")
@click.option("--fa", is_flag=True, help="Free agents only.")
@click.option("--sort", "sort_by", type=click.Choice(["dynasty", "now"]), default="dynasty")
@click.pass_context
def values(ctx, top, pos, fa, sort_by):
    """Player value rankings."""
    league = _load(ctx)
    ps = league.free_agents() if fa else list(league.players.values())
    if pos:
        ps = [p for p in ps if pos.upper() in p.positions]
    key = (lambda p: p.dynasty_value) if sort_by == "dynasty" else (lambda p: p.value)
    ps = [p for p in ps if p.has_projection or p.fv or p.prospect_rank]
    teams_by_id = {t.id: t.name for t in league.teams.values()}
    for i, p in enumerate(sorted(ps, key=key, reverse=True)[:top], 1):
        owner = teams_by_id.get(league.owner_of(p.id) or "", "FA") if league.teams else ""
        cats = " ".join(f"{c}{z:+.1f}" for c, z in p.cat_values.items())
        age = f"{p.age:.0f}" if p.age else "?"
        click.echo(f"{i:>3}. {str(p):<40} age {age:<3} now {p.value:+5.1f} "
                   f"dyn {p.dynasty_value:5.1f}  {owner:<18} {cats}")


@main.command()
@click.option("--with", "with_team", required=True, help="Trade partner (team name or id).")
@click.option("--give", default="", help="Comma-separated players you send.")
@click.option("--get", "get_", default="", help="Comma-separated players you receive.")
@click.option("--give-pick", multiple=True, help="Pick you send: YEAR:ROUND[:ORIG_TEAM]. Repeatable.")
@click.option("--get-pick", multiple=True, help="Pick you receive: YEAR:ROUND[:ORIG_TEAM]. Repeatable.")
@click.option("--team", default=None, help="Analyze as this team instead of yours.")
@click.pass_context
def trade(ctx, with_team, give, get_, give_pick, get_pick, team):
    """Grade a trade offer for both sides and suggest counters."""
    league = _load(ctx)
    my_id = _my_team_id(league, team)
    their_id = league.find_team(with_team).id
    my_side = trade_mod.Side(my_id, _resolve_players(league, give), _resolve_picks(league, my_id, give_pick))
    their_side = trade_mod.Side(their_id, _resolve_players(league, get_),
                                _resolve_picks(league, their_id, get_pick))
    for p in my_side.players:
        if league.owner_of(p.id) != my_id:
            raise click.ClickException(f"{p.name} isn't on your roster")
    for p in their_side.players:
        if league.owner_of(p.id) != their_id:
            raise click.ClickException(f"{p.name} isn't on {league.teams[their_id].name}")
    r = trade_mod.analyze(league, my_side, their_side)
    me, them = league.teams[my_id], league.teams[their_id]

    click.echo(f"\n{me.name} ({r.my_mode})  <->  {them.name} ({r.their_mode})\n")
    click.echo(f"You get  {r.my_get:6.1f}   give {r.my_give:6.1f}   roster adj {r.my_roster_adj:+5.1f}"
               f"   NET {r.my_net:+6.1f}   grade {r.grade}")
    click.echo(f"They get {r.their_get:6.1f}   give {r.their_give:6.1f}   roster adj {r.their_roster_adj:+5.1f}"
               f"   NET {r.their_net:+6.1f}")
    if r.my_cuts:
        click.echo("You'd have to cut: " + ", ".join(p.name for p in r.my_cuts))
    click.echo("\nThis-season category impact (your lineup):")
    click.echo("  " + "  ".join(f"{c} {_fmt_delta(c, v)}" for c, v in r.cat_delta.items()))
    click.echo(f"\nVerdict: {r.verdict}")
    if r.counters:
        click.echo("Counter-offers that flip it in your favour:")
        for desc, mine, theirs in r.counters:
            click.echo(f"  - {desc}: your net {mine:+.1f}, theirs {theirs:+.1f}")


@main.command()
@click.option("--top", default=15, show_default=True)
@click.option("--team", default=None)
@click.pass_context
def trades(ctx, top, team):
    """Find 1-for-1 trades both teams should accept."""
    league = _load(ctx)
    my_id = _my_team_id(league, team)
    ideas = trade_mod.find_trades(league, my_id, top)
    if not ideas:
        click.echo("No mutually beneficial 1-for-1 trades found.")
    for _, give, get, tid, mine, theirs in ideas:
        click.echo(f"Send {give.name:<24} to {league.teams[tid].name:<22} for {get.name:<24}"
                   f" you {mine:+5.1f} / them {theirs:+5.1f}")


@main.command()
@click.option("--team", default=None)
@click.option("--fa-top", default=10, show_default=True, help="How many minor-league free agents to list.")
@click.pass_context
def prospects(ctx, team, fa_top):
    """Promote / stash / cut calls for your minor leaguers, plus top minor FAs."""
    league = _load(ctx)
    tid = _my_team_id(league, team)
    t = league.teams[tid]
    reports = prospects_mod.team_report(league, tid)
    used = sum(1 for r in t.roster if r.status == "MINORS")
    click.echo(f"{t.name}: {used}/{league.cfg.minors} minors slots used  ({t.mode})\n")
    for r in reports:
        click.echo(f"  {r.action:<15} {str(r.player):<40} dyn {r.dynasty_value:5.1f}  "
                   f"[{r.eligibility}] {r.reason}")
    click.echo("\nBest minor-league free agents:")
    for p, v in prospects_mod.minor_free_agents(league, t.mode, fa_top):
        click.echo(f"  {str(p):<40} dyn {v:5.1f}  FV {p.fv or '?'}  ETA {p.eta or '?'}  {p.level}")


@main.command()
@click.option("--top", default=20, show_default=True)
@click.pass_context
def callups(ctx, top):
    """Prospects close to an MLB call-up, and who owns them."""
    league = _load(ctx)
    for p, owner, v in prospects_mod.callup_watch(league, top):
        name = league.teams[owner].name if owner else "FREE AGENT"
        click.echo(f"  {str(p):<40} dyn {v:5.1f}  ETA {p.eta}  {p.level or '?':<4} {name}")


def _week_ctx(league: League, two_start: str, games: tuple[str, ...]) -> WeekContext:
    ctx = WeekContext()
    for p in _resolve_players(league, two_start):
        ctx.starts[p.id] = 2
    for spec in games:
        name, _, n = spec.rpartition("=")
        if not name:
            raise click.ClickException(f"Bad --games '{spec}': use NAME=GAMES")
        ctx.hitter_games[league.find_player(name).id] = float(n)
    return ctx


@main.command()
@click.option("--vs", "vs", default=None, help="Opponent (default: this week's scheduled opponent).")
@click.option("--two-start", default="", help="Comma-separated SPs with two starts this week.")
@click.option("--games", multiple=True, help="Override games for a hitter: NAME=GAMES. Repeatable.")
@click.option("--team", default=None)
@click.pass_context
def lineup(ctx, vs, two_start, games, team):
    """Optimal weekly lineup to win the most categories."""
    league = _load(ctx)
    tid = _my_team_id(league, team)
    opp = league.find_team(vs).id if vs else league.opponent_this_week(tid)
    res = optimize(league, tid, opp, _week_ctx(league, two_start, games))
    if not res.starters:
        raise click.ClickException("No projections loaded, so there's nothing to set a lineup from. "
                                   "Add data/batters.csv and data/pitchers.csv (see README).")
    for slot, ps in res.starters.items():
        for p in ps:
            click.echo(f"  {slot:<4} {p}")
    click.echo("  BENCH " + ", ".join(p.name for p in res.bench))
    if res.probs:
        click.echo(f"\nvs {league.teams[opp].name}: expected categories won {res.expected_wins:.1f} / {len(res.probs)}")
        for c, pr in res.probs.items():
            click.echo(f"  {c:<5} you {_fmt_cat(c, res.my_totals[c]):>6}  them {_fmt_cat(c, res.opp_totals[c]):>6}"
                       f"  win {pr:5.0%}")
    else:
        click.echo("\n(No scheduled opponent found; pass --vs OPPONENT.)")


@main.command()
@click.option("--week", is_flag=True, help="Optimise for this week's matchup.")
@click.option("--vs", "vs", default=None)
@click.option("--prospects", "include_prospects", is_flag=True, help="Include prospects without MLB projections.")
@click.option("--top", default=15, show_default=True)
@click.option("--two-start", default="", help="Comma-separated SPs with two starts this week.")
@click.option("--team", default=None)
@click.pass_context
def waivers(ctx, week, vs, include_prospects, top, two_start, team):
    """Ranked waiver adds with suggested drops."""
    league = _load(ctx)
    tid = _my_team_id(league, team)
    if week:
        opp = league.find_team(vs).id if vs else league.opponent_this_week(tid)
        if not opp:
            raise click.ClickException("No scheduled opponent found; pass --vs OPPONENT")
        click.echo(f"Matchup: vs {league.teams[opp].name}")
        base, ideas = waivers_mod.week_targets(league, tid, opp,
                                               _week_ctx(league, two_start, ()), top)
        click.echo(f"Current expected categories won: {base:.2f}")
        for fa, drop, gain in ideas:
            click.echo(f"  ADD {str(fa):<40} DROP {drop.name:<24} {gain:+.2f} cats")
        return
    for i in waivers_mod.season_targets(league, tid, top, include_prospects):
        drop = i.drop.name if i.drop else "(open spot)"
        click.echo(f"  ADD {str(i.add):<40} DROP {drop:<24} score {i.score:+5.2f}  "
                   f"(season {i.season_gain:+.1f}, dynasty {i.dynasty_gain:+.1f})")


@main.command()
@click.pass_context
def status(ctx):
    """What data is loaded and what's missing."""
    league = _load(ctx)
    cfg = league.cfg
    n_proj = sum(1 for p in league.players.values() if p.has_projection)
    n_pros = sum(1 for p in league.players.values() if p.fv or p.prospect_rank)
    click.echo(f"League: {cfg.name}  ({cfg.teams} teams, season {cfg.season})")
    click.echo(f"Categories: {', '.join(cfg.categories)}")
    click.echo(f"Teams loaded: {len(league.teams)}   picks: {len(league.picks)}")
    click.echo(f"Players: {len(league.players)}   with projections: {n_proj}   prospects: {n_pros}")
    click.echo(f"My team: {cfg.my_team_id or 'NOT SET (league.toml my_team_id)'}")
    if league.unmatched:
        click.echo(f"Projection rows not matched to Fantrax players: {len(league.unmatched)} "
                   f"(e.g. {', '.join(league.unmatched[:5])})")


if __name__ == "__main__":
    sys.exit(main())
