"""
tips_engine.py — BetIntelHQ
Single daily scan. Scores each qualifying tip on a composite confidence model:
  1. Bookmaker consensus   — how many books agree the outcome is favourite
  2. Odds tightness        — closer to 1.30 = implied probability higher
  3. Line stability        — low spread between best and worst price = sharp market
Highest composite score wins.
"""

import os
import logging
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

API_KEY  = os.getenv("ODDS_API_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4/sports"
ATL      = ZoneInfo("America/Halifax")   # Halifax / Atlantic Time

SPORTS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_uefa_champs_league",
    "soccer_australia_aleague",
    "soccer_japan_j_league",
    "basketball_nba",
    "americanfootball_nfl",
    "baseball_mlb",
]

MIN_ODDS = 1.30
MAX_ODDS = 1.50


# ---------------------------------------------------------------------------
# Raw fetcher — 1 API call per sport (10 total per day)
# ---------------------------------------------------------------------------

def get_tips(sport: str) -> list:
    """Fetch upcoming h2h odds for one sport. Returns [] on any error."""
    if not API_KEY:
        logger.error("ODDS_API_KEY is not set.")
        return []

    try:
        resp = requests.get(
            f"{BASE_URL}/{sport}/odds/",
            params={
                "apiKey":     API_KEY,
                "regions":    "uk",
                "markets":    "h2h",
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("  %-40s → %d events", sport, len(data))
        return data
    except requests.RequestException as exc:
        logger.warning("  %-40s → failed: %s", sport, exc)
        return []


# ---------------------------------------------------------------------------
# Confidence scorer
# ---------------------------------------------------------------------------

def _score_candidate(
    outcome_name: str,
    bookmakers: list,
) -> tuple[float, float, float, int]:
    """
    Returns (composite_score, best_price, worst_price, book_count).

    Composite score components (each 0–1, equal weight):
      A. Consensus   — fraction of bookmakers that make this outcome favourite
      B. Probability — implied prob of best price, scaled within band
      C. Stability   — 1 - normalised price spread across books
    """
    prices_for_outcome = []
    favourite_count    = 0     # books where this outcome has lowest price (= favourite)

    for bk in bookmakers:
        bk_prices = {}   # outcome_name -> price for THIS bookmaker
        for market in bk.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for o in market.get("outcomes", []):
                try:
                    bk_prices[o["name"]] = float(o["price"])
                except (KeyError, TypeError, ValueError):
                    pass

        if outcome_name not in bk_prices:
            continue

        own_price = bk_prices[outcome_name]
        prices_for_outcome.append(own_price)

        # Is this the shortest-priced outcome at this book? → favourite
        if bk_prices and own_price == min(bk_prices.values()):
            favourite_count += 1

    if not prices_for_outcome:
        return 0.0, 0.0, 0.0, 0

    best_price  = min(prices_for_outcome)   # lowest = highest implied prob
    worst_price = max(prices_for_outcome)
    book_count  = len(bookmakers)

    # A. Consensus (0–1)
    consensus = favourite_count / book_count if book_count else 0.0

    # B. Probability — implied prob of best price, scaled within band
    #    implied_prob = 1 / best_price  (ignores vig but fine for ranking)
    imp_prob   = 1.0 / best_price
    band_lo    = 1.0 / MAX_ODDS    # ~0.667
    band_hi    = 1.0 / MIN_ODDS    # ~0.769
    band_range = band_hi - band_lo or 1e-9
    prob_score = max(0.0, min(1.0, (imp_prob - band_lo) / band_range))

    # C. Stability — lower spread across books = sharper market
    spread     = worst_price - best_price
    # normalise against max expected spread in band (MAX - MIN = 0.20)
    stability  = max(0.0, 1.0 - (spread / 0.20))

    composite = (consensus + prob_score + stability) / 3.0
    return composite, best_price, worst_price, len(prices_for_outcome)


# ---------------------------------------------------------------------------
# Best-tip selector — called once per day
# ---------------------------------------------------------------------------

def get_best_tip() -> dict | None:
    """
    Scan all 10 sports (10 API calls) and return today's single best tip.

    An outcome qualifies if:
      • Its best available price is in [1.30, 1.50]
      • It appears in at least 2 bookmakers (filters noise)
      • Kickoff is today (Atlantic Time)

    Among qualifiers, the highest composite confidence score wins.

    Returns:
        dict { sport, home_team, away_team, pick, odds, kickoff_atl,
               bookmaker, book_count, confidence }
        or None.
    """
    today_atl  = datetime.now(ATL).date()
    candidates = []

    logger.info("Daily scan — %d sports", len(SPORTS))

    for sport in SPORTS:
        for event in get_tips(sport):
            # --- kickoff ---
            raw = event.get("commence_time", "")
            try:
                kickoff_atl = datetime.fromisoformat(
                    raw.replace("Z", "+00:00")
                ).astimezone(ATL)
            except ValueError:
                continue

            if kickoff_atl.date() != today_atl:
                continue

            home       = event.get("home_team", "Unknown")
            away       = event.get("away_team", "Unknown")
            bookmakers = event.get("bookmakers", [])
            if len(bookmakers) < 2:
                continue   # need at least 2 books for a meaningful consensus

            # Find best price per outcome across all bookmakers
            best_price_map: dict[str, dict] = {}  # name -> {price, bookmaker}
            for bk in bookmakers:
                bk_name = bk.get("title", "Unknown")
                for market in bk.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    for o in market.get("outcomes", []):
                        name = o.get("name", "")
                        try:
                            price = float(o["price"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        if price > best_price_map.get(name, {}).get("price", 0):
                            best_price_map[name] = {
                                "price":     price,
                                "bookmaker": bk_name,
                            }

            for outcome_name, info in best_price_map.items():
                best_price = info["price"]
                if not (MIN_ODDS <= best_price <= MAX_ODDS):
                    continue

                score, _, _, book_count = _score_candidate(outcome_name, bookmakers)

                if book_count < 2:
                    continue

                candidates.append({
                    "sport":       sport,
                    "home_team":   home,
                    "away_team":   away,
                    "pick":        outcome_name,
                    "odds":        best_price,
                    "kickoff_atl": kickoff_atl,
                    "bookmaker":   info["bookmaker"],
                    "book_count":  book_count,
                    "confidence":  round(score * 100, 1),  # 0–100 %
                })

    if not candidates:
        logger.info("Scan complete — no qualifying tip today.")
        return None

    best = max(candidates, key=lambda c: c["confidence"])
    logger.info(
        "Best tip: %s @ %.2f | confidence %.1f%% | %s vs %s | KO %s",
        best["pick"], best["odds"], best["confidence"],
        best["home_team"], best["away_team"],
        best["kickoff_atl"].strftime("%I:%M %p AT"),
    )
    return best


# ---------------------------------------------------------------------------
# Manual test  →  python tips_engine.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tip = get_best_tip()
    if tip:
        for k, v in tip.items():
            print(f"  {k}: {v}")
    else:
        print("No qualifying tip found today.")
