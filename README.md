# Fantasy GM

A virtual general manager for a 16-team head-to-head categories dynasty
league on Fantrax, with minor leagues and tradeable rookie draft picks.

**Categories:** HR, AVG, RBI, SB · ERA, W, SV+HLD, WHIP, K

## What it does

| Command | Job |
|---|---|
| `fgm trade` | Grades a trade for **both** sides, using each team's contending/rebuilding mode. It charges for players a team has to cut, shows this season's category impact, and suggests counter-offers. |
| `fgm trades` | Finds 1-for-1 trades both teams should accept. These come from mode differences (a contender pays more for a rebuilder's veteran) and from how well a player fits your weak categories. |
| `fgm prospects` | Gives a PROMOTE / STASH / WATCH / CUT / ACTIVATE call for each of your minor leaguers, tracks how much of their minors eligibility they've used, and lists the best minor-league free agents. |
| `fgm callups` | Lists prospects at AAA whose ETA is this season, and who owns them. |
| `fgm lineup --vs TEAM` | Builds the legal weekly lineup that maximizes expected categories won against this week's opponent. Two-start pitchers are supported. |
| `fgm waivers` | Ranks waiver adds with a suggested drop. Season mode blends category fit with dynasty value by team mode. `--week --vs TEAM` ranks by the change in expected categories won this week. |
| `fgm cutdown` | Who to drop to get under the 48-man limit before the season, plus the players just above the cut line. |
| `fgm values` / `roster` / `teams` / `status` | Rankings, rosters with pick values, team modes, and a check of what data is loaded. |

## Setup

```bash
pip install -e ".[dev]"
```

### 1. League data from Fantrax

```bash
fgm sync
```

This pulls league info, rosters (active, bench, minors and IR), draft-pick
ownership and the player list into `data/fantrax/`. Every other command
works offline from that cache.

If the league is private, Fantrax needs a logged-in session. Copy the
`Cookie` request header from a logged-in fantrax.com tab in your browser's
dev tools into a `.fantrax_cookie` file (gitignored), or set
`FANTRAX_COOKIE`.

Then run `fgm teams`, find your team id, and put it in `league.toml` as
`my_team_id`.

### 2. Projections and prospects (CSV files in `data/`)

| File | Source suggestion | Needed columns |
|---|---|---|
| `batters.csv` | FanGraphs Steamer/ZiPS batters export | Name, Team, Age, PA, AB, H (or AVG), HR, RBI, SB |
| `pitchers.csv` | FanGraphs Steamer/ZiPS pitchers export | Name, Team, Age, IP, ER or ERA, BB, H (or WHIP), W, SV, HLD, SO, GS, G |
| `prospects.csv` | FanGraphs *The Board* export or any list | Name, Org, Pos, Age, FV, ETA, Level (Rank is accepted in place of FV) |
| `career.csv` *(optional)* | MLB career totals | Name, AB, IP, used for minors eligibility |

Columns are matched loosely, so exports from other sites work too. Run
`fgm status` to see how many projection rows matched a Fantrax player.

### 3. Fill in `league.toml`

Settings marked `TODO` need your league's real values:
- **Roster slots.** `fgm sync` overwrites these if Fantrax returns them.
- **Bench, minors and IR counts.**
- **The minors-eligibility rule** (`max_career_ab` / `max_career_ip`).
- **Team mode overrides** (`[team_modes]`), if the automatic
  contending/balanced/rebuilding call is wrong for a team.

## Examples

```bash
fgm trade --with "Team Name" --give "Player A,Player B" --get "Player C" --get-pick 2027:1
fgm lineup --vs "Opponent" --two-start "Pitcher X,Pitcher Y" --games "Hitter Z=4"
fgm waivers --week --vs "Opponent"
fgm prospects
fgm values --pos SS --fa --top 20
```

## How the values work

- **Current-season value** (`valuation.py`). Each category becomes a
  z-score against the pool of startable players (16 teams × active
  slots).
  - AVG counts hits above the pool average, so playing time matters.
  - ERA and WHIP count earned runs and baserunners saved against the pool
    rate.
  - A player's value is his total minus replacement level, which is lower
    at scarce positions.
- **Dynasty value** (`dynasty.py`). Projected value is aged along
  hitter/pitcher curves for 5 seasons, floored at zero each year (you can
  always cut a player), and discounted by team mode: contenders weight
  this year heavily and rebuilders weight the future.
- **Prospects.** FV maps to the current value of the MLB player at a
  matching overall rank (FV 60 ≈ the 70th-best player), multiplied by a
  hit rate and delayed until his ETA. This keeps prospects on the same
  scale as everyone else.
- **Draft picks.** Pick *k* is worth the *k*-th best unrostered prospect.
  When the slot isn't set yet, it's projected from the original owner's
  current strength. Future years carry an uncertainty discount.
- **Category needs** (`teamstats.py`). Your rank in each category weights
  trade and waiver fit. Gains in a category you're 14th in count for more
  than gains where you're already 1st.

## Limitations

- **Fantrax's API isn't officially documented.** The parsers are defensive,
  but a format change can break `fgm sync`. Raw responses are kept in
  `data/fantrax/` for debugging.
- **No schedule feed yet.** Weekly projections assume 6 games per hitter
  and one start per SP, unless you pass `--two-start` / `--games`.
- **This week's opponent isn't detected yet.** Pass `--vs` yourself.
- **Two-way players are matched to one side** (hitter or pitcher).

## Tests

```bash
pytest
```
