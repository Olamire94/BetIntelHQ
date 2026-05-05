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

SOCCER_SPORTS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_uefa_champs_league",
]

MIN_VALUE_EDGE   = 5.0
MAX_TIPS_PER_RUN = 5
MIN_ODDS         = 1.5
MAX_ODDS         = 4.0


def decimal_to_implied_prob(odds):
    return 1.0 / odds if odds > 0 else 0.0


def remove_vig(probs):
    total = sum(probs)
    return [p / total for p in probs] if total else probs


def value_edge(fair_prob, decimal_odds):
    return (fair_prob * decimal_odds - 1) * 100


async def fetch_odds(sport, session):
    is_soccer = sport in SOCCER_SPORTS
    markets = "h2h,totals,spreads"
    if is_soccer:
        markets = "h2h,totals,btts,spreads"

    url = (
        ODDS_API_BASE + "/sports/" + sport + "/odds"
        + "?apiKey=" + ODDS_API_KEY
        + "&regions=uk,eu"
        + "&markets=" + markets
        + "&oddsFormat=decimal"
        + "&dateFormat=iso"
    )
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                return await resp.json(), sport
            logger.warning("Odds API %s returned %d", sport, resp.status)
    except Exception as exc:
        logger.error("fetch_odds error (%s): %s", sport, exc)
    return [], sport


def fmt_date(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    except Exception:
        return iso


def get_all_outcomes(bookmakers):
    outcome_map = {}
    for bm in bookmakers:
        for market in bm.get("markets", []):
            mkey = market.get("key")
            for o in market.get("outcomes", []):
                name = o["name"]
                price = o["price"]
                point = o.get("point", None)

                if mkey == "totals":
                    label = name + " " + str(point) + " goals/runs"
                elif mkey == "btts":
                    label = "BTTS " + name
                elif mkey == "spreads":
                    sign = "+" if point and point > 0 else ""
                    label = name + " handicap " + sign + str(point)
                else:
                    label = name + " to win"

                if label not in outcome_map:
                    outcome_map[label] = []
                outcome_map[label].append(price)
    return outcome_map


def analyse_event(event):
    bookmakers = event.get("bookmakers", [])
    if not bookmakers:
        return None

    home = event["home_team"]
    away = event["away_team"]
    outcome_map = get_all_outcomes(bookmakers)

    if len(outcome_map) < 2:
        return None

    outcome_keys = list(outcome_map.keys())
    avg_odds_list = [sum(outcome_map[k]) / len(outcome_map[k]) for k in outcome_keys]
    raw_probs = [decimal_to_implied_prob(o) for o in avg_odds_list]

    total_prob = sum(raw_probs)
    if total_prob == 0:
        return None
    fair_probs = [p / total_prob for p in raw_probs]

    best_tip = None
    best_edge = MIN_VALUE_EDGE

    for i, label in enumerate(outcome_keys):
        prices = outcome_map[label]
        best_price = max(prices)

        if best_price < MIN_ODDS or best_price > MAX_ODDS:
            continue

        edge = value_edge(fair_probs[i], best_price)
        if edge > best_edge:
            best_edge = edge
            conf = min(5, max(1, int(edge / 4)))
            fp_pct = str(round(fair_probs[i] * 100, 1))
            ip_pct = str(round(decimal_to_implied_prob(best_price) * 100, 1))
            reasoning = (
                "Fair probability " + fp_pct + "% vs implied "
                + ip_pct + "%. Edge: +" + str(round(edge, 1)) + "%."
            )
            best_tip = {
                "match":      home + " vs " + away,
                "league":     event.get("sport_title", ""),
                "date":       fmt_date(event.get("commence_time", "")),
                "tip":        label,
                "odds":       round(best_price, 2),
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
    for result in results:
        events, sport = result
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
