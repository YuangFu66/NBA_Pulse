import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from prediction import (
    MAX_RECOMMENDATIONS,
    build_candidates,
    candidate_to_dict,
    select_top,
)


API_HOST = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/"
REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "docs" / "daily-data.json"
HISTORY_PATH = REPO_ROOT / "docs" / "picks-history.json"


def fetch_odds(api_key: str) -> list:
    query = urlencode(
        {
            "apiKey": api_key,
            "regions": "us",
            "bookmakers": "draftkings",
            "markets": "h2h,spreads",
            "oddsFormat": "american",
        }
    )
    with urlopen(f"{API_HOST}?{query}") as response:
        return json.loads(response.read().decode("utf-8"))


def format_record(event: dict) -> dict:
    return {
        "home_team": event["home_team"],
        "away_team": event["away_team"],
        "commence_time": event.get("commence_time"),
    }


def is_today_la(commence_time: str) -> bool:
    """Return True if the game starts today in Los Angeles time."""
    la = ZoneInfo("America/Los_Angeles")
    today = datetime.now(la).date()
    try:
        game_dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        return game_dt.astimezone(la).date() == today
    except (ValueError, AttributeError):
        return False


def build_payload(events: list) -> dict:
    now = datetime.now(timezone.utc)

    # Only consider games happening today (LA time)
    todays_events = [e for e in events if is_today_la(e.get("commence_time", ""))]

    all_candidates = []
    analyzed_games = []
    for event in todays_events:
        analyzed_games.append(format_record(event))
        all_candidates.extend(build_candidates(event))

    top = [candidate_to_dict(c) for c in select_top(all_candidates, MAX_RECOMMENDATIONS)]

    return {
        "generated_at": now.isoformat(),
        "generated_label": now.astimezone().strftime("%b %-d, %Y %I:%M %p %Z"),
        "bookmaker_title": "DraftKings",
        "games_analyzed": analyzed_games,
        "recommendations": top,
    }


def parse_pick(rec: dict, games: list, generated_at: str) -> dict | None:
    """Parse a recommendation into a pick record for the history file."""
    title = rec["title"]
    matchup = rec["matchup"]

    parts = matchup.split(" at ")
    away_team = parts[0].strip()
    home_team = parts[1].strip()

    pick_type = "moneyline"
    spread_point = None
    team = None

    ml_match = re.match(r"Buy (.+?) to win", title)
    if ml_match:
        team = ml_match.group(1)
    else:
        sp_match = re.match(r"Buy (.+?) ([+-]\d+(?:\.\d+)?)", title)
        if sp_match:
            team = sp_match.group(1)
            spread_point = float(sp_match.group(2))
            pick_type = "spread"

    if not team:
        return None

    commence_time = None
    for game in games:
        if game["home_team"] == home_team and game["away_team"] == away_team:
            commence_time = game.get("commence_time")
            break

    return {
        "generated_at": generated_at,
        "team": team,
        "type": pick_type,
        "spread_point": spread_point,
        "title": title,
        "line": rec["line"],
        "matchup": matchup,
        "home_team": home_team,
        "away_team": away_team,
        "commence_time": commence_time,
        "result": "pending",
        "home_score": None,
        "away_score": None,
    }


def append_picks_to_history(payload: dict) -> None:
    """Append today's recommendations to picks-history.json."""
    if HISTORY_PATH.exists():
        history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    else:
        history = {"picks": []}

    generated_at = payload["generated_at"]
    games = payload.get("games_analyzed", [])
    existing_keys = {(p["generated_at"], p["title"]) for p in history["picks"]}

    for rec in payload.get("recommendations", []):
        pick = parse_pick(rec, games, generated_at)
        if not pick:
            continue
        key = (pick["generated_at"], pick["title"])
        if key in existing_keys:
            continue
        history["picks"].append(pick)

    HISTORY_PATH.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        raise SystemExit("ODDS_API_KEY is required")

    events = fetch_odds(api_key)
    payload = build_payload(events)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    append_picks_to_history(payload)


if __name__ == "__main__":
    main()
