"""Prediction skill for NBA Pulse.

Generates value-based betting recommendations from DraftKings odds. For each
game we evaluate all four candidate bets (home/away moneyline, home/away
spread), estimate each side's true win/cover probability, then keep only
positive-expected-value bets ranked by EV.

The model has three layers:

1. Market consensus: remove the bookmaker's vig from each market separately
   (h2h and spreads have different juice) to get a fair win/cover probability.

2. Home-court adjustment: NBA home court is consistently underpriced by the
   market. Empirically (see scripts/check_accuracy.py output) home picks have
   gone +24% ROI vs -26% ROI for away picks across our history. We add a
   small probability bump to the home side and subtract from the away side.

3. Empirical bucket adjustments: small, capped tweaks for historical
   loser buckets (the -140..-200 ML "dead zone", big-underdog spreads, and
   heavy chalk) and winner buckets (mid-range home favorite spreads).

The final filter is expected value, not score-on-line. We only surface a
candidate if EV >= MIN_EV_PER_DOLLAR after adjustments.
"""

from __future__ import annotations

from dataclasses import dataclass


# --- Tunable knobs -----------------------------------------------------------

HOME_COURT_PROB = 0.025
"""Probability we add to the home team / subtract from the away team. NBA
home court is worth ~2.5-3 points historically; the line already prices most
of it, but the residual edge has shown up consistently in our picks."""

MIN_EV_PER_DOLLAR = 0.005
"""Minimum expected value per $1 wagered. NBA markets are tight enough that
a 2.5%-prob edge from home court typically produces only ~0.5-1.5% EV after
the vig is removed; a higher threshold filters out almost everything. We
lean on the empirical adjustments (home/bucket) to drive selection, and use
EV >= +0.5% as a soft sanity check that a non-trivial edge exists."""

MIN_ML_WIN_PROB = 0.35
"""Don't recommend moneyline underdogs whose adjusted win probability is
below this. Longshot ML bets can be +EV but introduce a lot of bankroll
variance, which is a poor user experience even when the math works."""

MAX_RECOMMENDATIONS = 3


# --- Odds math ---------------------------------------------------------------

def american_to_probability(price: int) -> float:
    if price < 0:
        return (-price) / ((-price) + 100)
    return 100 / (price + 100)


def american_to_decimal(price: int) -> float:
    if price < 0:
        return 1 + 100 / abs(price)
    return 1 + price / 100


def no_vig_pair(price_a: int, price_b: int) -> tuple[float, float]:
    """Strip the bookmaker's vig from a two-outcome market and return
    (fair_prob_a, fair_prob_b)."""
    pa = american_to_probability(price_a)
    pb = american_to_probability(price_b)
    total = pa + pb
    return pa / total, pb / total


def expected_value(prob: float, price: int) -> float:
    """Return EV per $1 staked at the given American price and true prob."""
    decimal = american_to_decimal(price)
    return prob * (decimal - 1) - (1 - prob)


# --- Empirical adjustments ---------------------------------------------------

def empirical_prob_adjustment(bet_type: str, side: str, price: int,
                              spread_point: float | None) -> float:
    """Return an additive probability adjustment based on historical buckets.

    All numbers are intentionally small (<= 0.04) to avoid overfitting on a
    ~100-pick history. They nudge, not override, the market.
    """
    adj = 0.0

    if side == "home":
        adj += HOME_COURT_PROB
    else:
        adj -= HOME_COURT_PROB

    if bet_type == "moneyline":
        # The -140..-200 range has been a money-loser; chalk-heavy ML payouts
        # don't compensate when the favorite stumbles. Heavy chalk (<-200)
        # is similar but worse on payout.
        if -200 <= price <= -140:
            adj -= 0.025
        elif price < -200:
            adj -= 0.020

    if bet_type == "spread" and spread_point is not None:
        # Big underdog spreads (>= 12) have lost badly: NBA blowouts cover.
        if spread_point >= 12:
            adj -= 0.035
        # Mid-range home favorites (-3.5 to -7) have been the strongest
        # bucket. Small bonus when both side==home and point in this range.
        if side == "home" and -7.5 <= spread_point <= -3.0:
            adj += 0.020

    return adj


# --- Candidate generation ----------------------------------------------------

@dataclass
class Candidate:
    score: float          # EV in % (i.e. EV per $1 * 100)
    ev: float             # raw EV per $1
    prob: float           # adjusted true probability
    fair_prob: float      # market no-vig probability (before our adjustments)
    title: str
    line: str
    matchup: str
    bet_type: str
    side: str             # "home" or "away"
    team: str
    price: int
    spread_point: float | None
    reason: str


def _market_dict(event: dict) -> dict:
    bookmaker = event["bookmakers"][0]
    return {market["key"]: market for market in bookmaker["markets"]}


def _format_price(price: int) -> str:
    return f"{price:+d}"


def _format_point(point: float) -> str:
    return f"{point:+g}"


def _build_reason(c: Candidate) -> str:
    edge_pct = c.ev * 100
    fair_pct = c.fair_prob * 100
    adj_pct = c.prob * 100
    home_or_away = "at home" if c.side == "home" else "on the road"

    if c.bet_type == "moneyline":
        return (
            f"Recommendation: buy {c.team} on the moneyline {home_or_away}. "
            f"Stripping the book's vig puts their fair win probability around "
            f"{fair_pct:.0f}%, and after adjusting for our home-court signal "
            f"and historical bucket performance we estimate {adj_pct:.0f}%. "
            f"At {_format_price(c.price)} that is roughly +{edge_pct:.1f}% "
            f"expected value per dollar, which is the threshold this model "
            f"requires before flagging a side."
        )

    point_str = _format_point(c.spread_point)
    cover_word = "cover" if c.spread_point < 0 else "stay within"
    return (
        f"Recommendation: buy {c.team} {point_str} {home_or_away}. "
        f"The no-vig spread market gives them about a {fair_pct:.0f}% chance "
        f"to {cover_word}, and after our home-court and bucket adjustments we "
        f"land near {adj_pct:.0f}%. Priced at {_format_price(c.price)}, that "
        f"works out to roughly +{edge_pct:.1f}% expected value per dollar, "
        f"so the side clears our minimum-edge filter."
    )


def build_candidates(event: dict) -> list[Candidate]:
    """Return every candidate bet for an event that has positive EV after
    adjustments. The caller decides which to keep."""
    markets = _market_dict(event)
    if "h2h" not in markets or "spreads" not in markets:
        return []

    home_team = event["home_team"]
    away_team = event["away_team"]
    matchup = f"{away_team} at {home_team}"

    h2h = {o["name"]: o["price"] for o in markets["h2h"]["outcomes"]}
    spreads = {
        o["name"]: {"point": o["point"], "price": o["price"]}
        for o in markets["spreads"]["outcomes"]
    }

    if home_team not in h2h or away_team not in h2h:
        return []
    if home_team not in spreads or away_team not in spreads:
        return []

    home_ml = h2h[home_team]
    away_ml = h2h[away_team]
    home_sp = spreads[home_team]
    away_sp = spreads[away_team]

    fair_home_ml, fair_away_ml = no_vig_pair(home_ml, away_ml)
    fair_home_cover, fair_away_cover = no_vig_pair(home_sp["price"], away_sp["price"])

    raw_candidates = [
        # Moneylines
        ("moneyline", "home", home_team, home_ml, None, fair_home_ml),
        ("moneyline", "away", away_team, away_ml, None, fair_away_ml),
        # Spreads
        ("spread", "home", home_team, home_sp["price"], home_sp["point"], fair_home_cover),
        ("spread", "away", away_team, away_sp["price"], away_sp["point"], fair_away_cover),
    ]

    candidates: list[Candidate] = []
    for bet_type, side, team, price, point, fair_prob in raw_candidates:
        adj = empirical_prob_adjustment(bet_type, side, price, point)
        prob = max(0.01, min(0.99, fair_prob + adj))
        ev = expected_value(prob, price)
        if ev < MIN_EV_PER_DOLLAR:
            continue
        if bet_type == "moneyline" and prob < MIN_ML_WIN_PROB:
            continue

        if bet_type == "moneyline":
            title = f"Buy {team} to win"
            line = _format_price(price)
        else:
            title = f"Buy {team} {_format_point(point)}"
            line = f"Spread {_format_point(point)} at {_format_price(price)}"

        c = Candidate(
            score=ev * 100,
            ev=ev,
            prob=prob,
            fair_prob=fair_prob,
            title=title,
            line=line,
            matchup=matchup,
            bet_type=bet_type,
            side=side,
            team=team,
            price=price,
            spread_point=point,
            reason="",
        )
        c.reason = _build_reason(c)
        candidates.append(c)

    return candidates


def select_top(candidates: list[Candidate], n: int = MAX_RECOMMENDATIONS) -> list[Candidate]:
    """Pick the highest-EV candidates while diversifying across games."""
    seen_matchups: set[str] = set()
    chosen: list[Candidate] = []
    for c in sorted(candidates, key=lambda x: x.score, reverse=True):
        if c.matchup in seen_matchups:
            continue
        chosen.append(c)
        seen_matchups.add(c.matchup)
        if len(chosen) == n:
            break
    return chosen


def candidate_to_dict(c: Candidate) -> dict:
    return {
        "score": round(c.score, 2),
        "title": c.title,
        "line": c.line,
        "matchup": c.matchup,
        "reason": c.reason,
    }
