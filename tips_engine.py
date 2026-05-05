import os
import asyncio
import logging
from datetime import datetime, timezone
import aiohttp

logger = logging.getLogger(__name__)

ODDS_API_KEY  = os.environ.get("ODDS_API_KEY", "").strip()
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

SPORTS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_uefa_champs_league",
    "basketball_nba",
    "americanfootball_nfl",
    "baseball_mlb",
]

MIN_VALUE_EDGE   = 2.0
MAX_TIPS_PER_RUN = 5
MIN_ODDS         = 1.5
MAX_ODDS         = 2.0


def decimal_to_implied_prob(odds):
    return 1.0 / odds if odds > 0 else 0.0


def remove_vig(probs):
    total = sum(probs)
    return [p / total for p in probs] if total else probs


def value_edge(fair_prob, decimal_odds):
    return (fair_prob * decimal_odds - 1) * 100


async def fetch_odds(sport, session):
    url = (
        ODDS_API_BASE + "/sports/" + sport + "/odds"
        + "?apiKey=" + ODDS_API_KEY
        + "&regions=uk,eu"
        + "&markets=h2h"
        + "&oddsFormat=decimal"
        + "&dateFormat=iso"
    )
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                return await resp.json()
            logger.warning("Odds API %s returned %d", sport, resp.status)
    except Exception as exc:
        logger.error("fetch_odds error (%s): %s", sport, exc)
    return []


def best_odds_for_outcome(bookmakers, outcome_name):
    best = None
    for bm in bookmakers:
        for market in bm.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for o in market.get("outcomes", []):
                if o["name"] == outcome_name:
                    if best is None or o["price"] > best["price"]:
                        best = {"bookmaker": bm["title"], "price": o["price"]}
    return best


def fmt_date(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    except Exception:
        return iso


def analyse_event(event):
    bookmakers = event.get("bookmakers", [])
    if not bookmakers:
        return None

    home = event["home_team"]
    away = event["away_team"]
    outcomes = [home, away, "Draw"]

    avg_odds = {}
    for outcome in outcomes:
        prices = []
        for bm in bookmakers:
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for o in market.get("outcomes", []):
                    if o["name"] == outcome:
                        prices.append(o["price"])
        if prices:
            avg_odds[outcome] = sum(prices) / len(prices)

    if len(avg_odds) < 2:
        return None

    raw_probs = [decimal_to_implied_prob(avg_odds[k]) for k in avg_odds]
    fair_probs = remove_vig(raw_probs)
    outcome_keys = list(avg_odds.keys())

    best_tip = None
    best_edge = MIN_VALUE_EDGE

    for i, outcome in enumerate(outcome_keys):
        best = best_odds_for_outcome(bookmakers, outcome)
        if not best:
            continue
        if best["price"] < MIN_ODDS or best["price"] > MAX_ODDS:
            continue
        edge = value_edge(fair_probs[i], best["price"])
        if edge > best_edge:
            best_edge = edge
            conf = min(5, max(1, int(edge / 4)))
            tip_label = outcome + " to win" if outcome != "Draw" else "Draw"
            fp_pct = str(round(fair_probs[i] * 100, 1))
            ip_pct = str(round(decimal_to_implied_prob(best["price"]) * 100, 1))
            reasoning = (
                "Fair probability " + fp_pct + "% vs implied "
                + ip_pct + "% at " + best["bookmaker"]
                + ". Edge: +" + str(round(edge, 1)) + "%."
            )
            best_tip = {
                "match":      home + " vs " + away,
                "league":     event.get("sport_title", ""),
                "date":       fmt_date(event.get("commence_time", "")),
                "tip":        tip_label,
                "odds":       round(best["price"], 2),
                "bookmaker":  best["bookmaker"],
                "value_edge": round(edge, 1),
                "confidence": conf,
                "reasoning":  reasoning,
            }

    return best_tip


async def get_tips():
    all_tips = []
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_odds(sport, session) for sport in SPORTS]
        results = await asyncio.gather(*tasks)
    for events in results:
        for event in events:
            tip = analyse_event(event)
            if tip:
                all_tips.append(tip)
    all_tips.sort(key=lambda t: t["value_edge"], reverse=True)
    return all_tips[:MAX_TIPS_PER_RUN]


async def get_daily_summary():
    tips = await get_tips()
    if not tips:
        lines = [
            "Daily Summary",
            "",
            "No value bets found today.",
            "Markets look sharp - patience pays.",
        ]
        return "\n".join(lines)

    avg_edge = sum(t["value_edge"] for t in tips) / len(tips)
    avg_odds = sum(t["odds"] for t in tips) / len(tips)

    lines = [
        "Daily Summary - " + datetime.now().strftime("%d %b %Y"),
        "",
        "Value bets found: " + str(len(tips)),
        "Avg value edge: +" + str(round(avg_edge, 1)) + "%",
        "Avg best odds: " + str(round(avg_odds, 2)),
        "",
        "Top picks today:",
    ]
    for i, t in enumerate(tips, 1):
        lines.append(
            str(i) + ". " + t["match"] + " - " + t["tip"]
            + " @ " + str(t["odds"]) + " (+" + str(t["value_edge"]) + "%)"
        )
    lines.append("")
    lines.append("Gamble responsibly.")
    return "\n".join(lines)
