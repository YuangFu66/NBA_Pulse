import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


API_HOST = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/"
REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "site" / "daily-data.json"


def american_to_probability(price: int) -> float:
    if price < 0:
        return (-price) / ((-price) + 100)
    return 100 / (price + 100)


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


def pick_outcomes(event: dict) -> dict:
    bookmaker = event["bookmakers"][0]
    markets = {market["key"]: market for market in bookmaker["markets"]}
    h2h = {outcome["name"]: outcome["price"] for outcome in markets["h2h"]["outcomes"]}
    spreads = {
        outcome["name"]: {"point": outcome["point"], "price": outcome["price"]}
        for outcome in markets["spreads"]["outcomes"]
    }
    return {"bookmaker": bookmaker, "h2h": h2h, "spreads": spreads}


def build_candidates(event: dict) -> list:
    parsed = pick_outcomes(event)
    teams = [event["home_team"], event["away_team"]]
    teams.sort(key=lambda team: american_to_probability(parsed["h2h"][team]), reverse=True)
    favorite, underdog = teams[0], teams[1]

    favorite_ml = parsed["h2h"][favorite]
    favorite_spread = parsed["spreads"][favorite]
    underdog_spread = parsed["spreads"][underdog]

    matchup = f"{event['away_team']} at {event['home_team']}"
    candidates = []

    if -170 <= favorite_ml <= -110:
        score = 100 - abs(abs(favorite_ml) - 135) * 0.35
        candidates.append(
            {
                "score": score,
                "title": f"{favorite} Moneyline",
                "line": f"{favorite_ml:+d}",
                "matchup": matchup,
                "reason": (
                    f"{favorite} is a short moneyline favorite, which keeps the market edge intact "
                    f"without asking for extra margin beyond a straight-up win."
                ),
            }
        )

    if favorite_spread["point"] <= -1.5 and favorite_spread["point"] >= -6.5:
        score = 92 - abs(abs(favorite_spread["point"]) - 3.5) * 7 - max(0, abs(favorite_spread["price"]) - 115)
        candidates.append(
            {
                "score": score,
                "title": f"{favorite} {favorite_spread['point']:+g}",
                "line": f"Spread {favorite_spread['point']:+g} at {favorite_spread['price']:+d}",
                "matchup": matchup,
                "reason": (
                    f"The spread stays in a controlled range, which is usually the cleanest zone for favorites "
                    f"that already have aligned support from the moneyline."
                ),
            }
        )

    if underdog_spread["point"] >= 9.5:
        score = 84 - abs(underdog_spread["point"] - 11.5) * 4 - max(0, abs(underdog_spread["price"]) - 112)
        candidates.append(
            {
                "score": score,
                "title": f"{underdog} +{underdog_spread['point']:g}",
                "line": f"Spread +{underdog_spread['point']:g} at {underdog_spread['price']:+d}",
                "matchup": matchup,
                "reason": (
                    f"Double-digit cushion at near-standard juice can be valuable in volatile NBA games, "
                    f"especially when the favorite must win by a large margin to cash."
                ),
            }
        )

    return candidates


def build_payload(events: list) -> dict:
    now = datetime.now(timezone.utc)
    candidates = []
    for event in events:
        candidates.extend(build_candidates(event))

    top = []
    seen_matchups = set()
    for item in sorted(candidates, key=lambda entry: entry["score"], reverse=True):
        if item["matchup"] in seen_matchups:
            continue
        top.append(item)
        seen_matchups.add(item["matchup"])
        if len(top) == 3:
            break

    return {
        "generated_at": now.isoformat(),
        "generated_label": now.astimezone().strftime("%b %-d, %Y %I:%M %p %Z"),
        "bookmaker_title": "DraftKings",
        "recommendations": top,
    }


def main() -> None:
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        raise SystemExit("ODDS_API_KEY is required")

    events = fetch_odds(api_key)
    payload = build_payload(events)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
