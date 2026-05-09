"""
tips_engine.py — BetIntelHQ
Single daily scan: fetches odds across all sports, returns the best tip.
"""

import os
import logging
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

API_KEY  = os.getenv("ODDS_API_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4/sports"

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

MIN_ODDS     = 1.30
MAX_ODDS     = 1.50
BAND_MID     = (MIN_ODDS + MAX_ODDS) / 2  # 1.40
EST          = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Raw fetcher — 1 API call per sport
# ---------------------------------------------------------------------------

def get_tips(sport: str) -> list:
    """Fetch upcoming h2h odds for one sport. Returns [] on error."""
    if not API_KEY:
        logger.error("ODDS_API_KEY is not set.")
        return []

    try:
        resp = requests.get(
            f"{BASE_URL}/{sport}/odds/",
            params={
                "apiKey":      API_KEY,
                "regions":     "uk",
                "markets":     "h2h",
                "oddsFormat":  "decimal",
                "dateFormat":  "iso",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("  %s → %d events", sport, len(data))
        return data
    except requests.RequestException as exc:
        logger.warning("  %s → fetch failed: %s", sport, exc)
        return []


# ---------------------------------------------------------------------------
# Best-tip selector — called once per day
# ---------------------------------------------------------------------------

def get_best_tip() -> dict | None:
    """
    Scan all 10 sports (10 API calls total) and return the single best tip
    for TODAY whose odds fall in [1.30, 1.50].

    Selection: pick the outcome whose best available odds are closest to 1.40
    (band midpoint) — balances confidence with value.

    Returns a dict:
        sport, home_team, away_team, pick, odds, kickoff_est, bookmaker
    or None if nothing qualifies.
    """
    today_est = datetime.now(EST).date()
    candidates = []

    logger.info("Starting daily scan (%d sports)...", len(SPORTS))

    for sport in SPORTS:
        for event in get_tips(sport):
            # --- parse kickoff ---
            raw = event.get("commence_time", "")
            try:
                kickoff_est = datetime.fromisoformat(
                    raw.replace("Z", "+00:00")
                ).astimezone(EST)
            except ValueError:
                continue

            # today's games only
            if kickoff_est.date() != today_est:
                continue

            home = event.get("home_team", "Unknown")
            away = event.get("away_team", "Unknown")

            # best price per outcome across all bookmakers
            best: dict[str, dict] = {}   # outcome -> {price, bookmaker}

            for bk in event.get("bookmakers", []):
                bk_name = bk.get("title", "Unknown")
                for market in bk.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    for outcome in market.get("outcomes", []):
                        name = outcome.get("name", "")
                        try:
                            price = float(outcome["price"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        if price > best.get(name, {}).get("price", 0):
                            best[name] = {"price": price, "bookmaker": bk_name}

            for outcome_name, info in best.items():
                if MIN_ODDS <= info["price"] <= MAX_ODDS:
                    candidates.append({
                        "sport":       sport,
                        "home_team":   home,
                        "away_team":   away,
                        "pick":        outcome_name,
                        "odds":        info["price"],
                        "kickoff_est": kickoff_est,
                        "bookmaker":   info["bookmaker"],
                    })

    if not candidates:
        logger.info("Scan complete — no qualifying tip today.")
        return None

    best_tip = min(candidates, key=lambda c: abs(c["odds"] - BAND_MID))
    logger.info(
        "Best tip: %s @ %.2f [%s] — %s vs %s | KO %s",
        best_tip["pick"], best_tip["odds"], best_tip["sport"],
        best_tip["home_team"], best_tip["away_team"],
        best_tip["kickoff_est"].strftime("%H:%M EST"),
    )
    return best_tip


# ---------------------------------------------------------------------------
# Manual test  →  python tips_engine.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tip = get_best_tip()
    print(tip if tip else "No qualifying tip found today.")
