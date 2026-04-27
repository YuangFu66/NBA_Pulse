"""Check game results and resolve pending picks in picks-history.json.

Uses The Odds API scores endpoint for recent games (last 3 days) and the
ESPN scoreboard API for older games.  Writes updated picks-history.json
and a lightweight return-data.json consumed by the website.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = REPO_ROOT / "docs" / "picks-history.json"
RETURN_PATH = REPO_ROOT / "docs" / "return-data.json"

ODDS_API_HOST = "https://api.the-odds-api.com/v4/sports/basketball_nba/scores/"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"


# ---------------------------------------------------------------------------
# Score fetching helpers
# ---------------------------------------------------------------------------

def fetch_odds_api_scores(api_key: str) -> list:
    """Fetch completed NBA scores from The Odds API (last 3 days)."""
    query = urlencode({"apiKey": api_key, "daysFrom": "3"})
    try:
        with urlopen(f"{ODDS_API_HOST}?{query}") as resp:
            return json.loads(resp.read().decode())
    except (URLError, json.JSONDecodeError):
        return []


def fetch_espn_scores(date_str: str) -> list:
    """Fetch NBA scores from ESPN for a given date (YYYYMMDD)."""
    try:
        with urlopen(f"{ESPN_SCOREBOARD}?dates={date_str}") as resp:
            data = json.loads(resp.read().decode())
    except (URLError, json.JSONDecodeError):
        return []

    games = []
    for event in data.get("events", []):
        competitors = event.get("competitions", [{}])[0].get("competitors", [])
        if len(competitors) < 2:
            continue
        status = event.get("status", {}).get("type", {}).get("name", "")
        if status != "STATUS_FINAL":
            continue
        home = away = None
        for c in competitors:
            entry = {"team": c["team"]["displayName"], "score": int(c.get("score", 0))}
            if c["homeAway"] == "home":
                home = entry
            else:
                away = entry
        if home and away:
            games.append({"home_team": home["team"], "away_team": away["team"],
                          "home_score": home["score"], "away_score": away["score"]})
    return games


# ---------------------------------------------------------------------------
# Nickname / display-name mapping for matching across APIs
# ---------------------------------------------------------------------------

TEAM_ALIASES = {}

def _normalize(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def teams_match(a: str, b: str) -> bool:
    """Fuzzy team name match – works across Odds API / ESPN / pick data."""
    na, nb = _normalize(a), _normalize(b)
    if na == nb:
        return True
    # Check if the last word (nickname) matches
    if na.split()[-1] == nb.split()[-1]:
        return True
    # Check substring containment
    return na in nb or nb in na


# ---------------------------------------------------------------------------
# Resolve a single pick
# ---------------------------------------------------------------------------

def resolve_pick(pick: dict, home_score: int, away_score: int) -> str:
    team = pick["team"]
    is_home = teams_match(team, pick["home_team"])
    team_score = home_score if is_home else away_score
    opp_score = away_score if is_home else home_score

    if pick["type"] == "moneyline":
        if team_score > opp_score:
            return "win"
        elif team_score < opp_score:
            return "loss"
        return "push"
    else:
        margin = team_score - opp_score + pick["spread_point"]
        if margin > 0:
            return "win"
        elif margin < 0:
            return "loss"
        return "push"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    api_key = os.environ.get("ODDS_API_KEY", "")

    # Load existing history
    if HISTORY_PATH.exists():
        history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    else:
        history = {"picks": []}

    picks = history["picks"]
    pending = [p for p in picks if p["result"] == "pending"]

    if not pending:
        print("No pending picks to resolve.")
        write_return(picks)
        return

    # ----- Collect scores from both sources -----

    # 1) Odds API (last 3 days)
    odds_scores = {}
    if api_key:
        for game in fetch_odds_api_scores(api_key):
            if not game.get("completed"):
                continue
            scores = game.get("scores")
            if not scores:
                continue
            score_map = {s["name"]: int(s["score"]) for s in scores if s.get("score")}
            ht = game.get("home_team", "")
            at = game.get("away_team", "")
            if ht in score_map and at in score_map:
                odds_scores[(ht, at)] = (score_map[ht], score_map[at])

    # 2) ESPN – collect unique game dates for pending picks
    espn_scores = {}  # (home, away) -> (home_score, away_score)
    dates_needed = set()
    for p in pending:
        ct = p.get("commence_time")
        if ct:
            try:
                dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                # Check the game date and possibly the day before (late games)
                dates_needed.add(dt.strftime("%Y%m%d"))
                dates_needed.add((dt - timedelta(days=1)).strftime("%Y%m%d"))
            except ValueError:
                pass
        else:
            # No commence_time – try the generated_at date range
            ga = p.get("generated_at", "")
            if ga:
                try:
                    dt = datetime.fromisoformat(ga.replace("Z", "+00:00"))
                    for d in range(0, 3):
                        dates_needed.add((dt + timedelta(days=d)).strftime("%Y%m%d"))
                except ValueError:
                    pass

    for date_str in sorted(dates_needed):
        for g in fetch_espn_scores(date_str):
            espn_scores[(g["home_team"], g["away_team"])] = (g["home_score"], g["away_score"])

    # ----- Resolve pending picks -----
    resolved_count = 0
    for pick in pending:
        ht, at = pick["home_team"], pick["away_team"]

        # Try Odds API first, then ESPN
        score = None
        for source in [odds_scores, espn_scores]:
            for (sh, sa), (hs, as_) in source.items():
                if teams_match(sh, ht) and teams_match(sa, at):
                    score = (hs, as_)
                    break
            if score:
                break

        if not score:
            continue

        home_score, away_score = score
        pick["home_score"] = home_score
        pick["away_score"] = away_score
        pick["result"] = resolve_pick(pick, home_score, away_score)
        resolved_count += 1

    print(f"Resolved {resolved_count} picks, {len(pending) - resolved_count} still pending.")

    # Write updated history
    HISTORY_PATH.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")

    # Write return summary
    write_return(picks)


def parse_odds(pick: dict) -> int | None:
    """Extract American odds from a pick's line field."""
    line = pick.get("line", "")
    if pick["type"] == "moneyline":
        m = re.match(r"([+-]?\d+)", line)
        return int(m.group(1)) if m else None
    else:
        m = re.search(r"at\s+([+-]?\d+)", line)
        return int(m.group(1)) if m else None


def calc_profit(odds: int, result: str) -> float:
    """Return profit in units for a 1-unit wager."""
    if result == "push":
        return 0.0
    if result == "loss":
        return -1.0
    if odds < 0:
        return 100 / abs(odds)
    else:
        return odds / 100


def write_return(picks: list) -> None:
    resolved = [p for p in picks if p["result"] in ("win", "loss", "push")]
    wins = sum(1 for p in resolved if p["result"] == "win")
    losses = sum(1 for p in resolved if p["result"] == "loss")
    pushes = sum(1 for p in resolved if p["result"] == "push")
    pending_count = sum(1 for p in picks if p["result"] == "pending")

    total_profit = 0.0
    units_wagered = 0
    for p in resolved:
        odds = parse_odds(p)
        if odds is None:
            continue
        total_profit += calc_profit(odds, p["result"])
        units_wagered += 1

    return_pct = round(total_profit / units_wagered * 100, 1) if units_wagered else 0
    total_profit = round(total_profit, 2)

    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_profit": total_profit,
        "units_wagered": units_wagered,
        "return_pct": return_pct,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "pending": pending_count,
        "total_picks": len(picks),
        "record_label": f"{wins}-{losses}" + (f"-{pushes}" if pushes else ""),
    }

    RETURN_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Return: {'+' if total_profit >= 0 else ''}{total_profit}u ({'+' if return_pct >= 0 else ''}{return_pct}%) | Record: {data['record_label']}")


if __name__ == "__main__":
    main()
