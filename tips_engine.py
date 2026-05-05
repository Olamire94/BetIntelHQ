"""
tips_engine.py
──────────────
Fetches upcoming matches + odds from the free Odds API, calculates value edges,
and returns the best bets of the day.
Free API key: https://the-odds-api.com (500 requests/month on free tier)
"""
import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
import aiohttp
logger = logging.getLogger(__name__)
# ── Config ────────────────────────────────────────────────────────────────────
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "YOUR_ODDS_API_KEY_HERE")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
# Sports to monitor (full list: https://the-odds-api.com/sports-odds-data/sports-apis.html)
SPORTS = [
"soccer_epl",
"soccer_spain_la_liga",
"soccer_germany_bundesliga",
"soccer_italy_serie_a",
"soccer_uefa_champs_league",
"basketball_nba",
"americanfootball_nfl",
]
MIN_VALUE_EDGE = 5.0 MAX_TIPS_PER_RUN = 5 # % — only show bets with >5% edge
# cap tips per broadcast
# ── Core maths ────────────────────────────────────────────────────────────────
def decimal_to_implied_prob(odds: float) -> float:
"""Odds → implied probability (0-1)."""
return 1.0 / odds if odds > 0 else 0.0
def remove_vig(probs: list[float]) -> list[float]:
"""Strip the bookmaker margin to get fair probabilities."""
total = sum(probs)
return [p / total for p in probs] if total else probs
def value_edge(fair_prob: float, decimal_odds: float) -> float:
"""Expected value edge as a percentage."""
return (fair_prob * decimal_odds - 1) * 100
# ── Fetching ──────────────────────────────────────────────────────────────────
async def fetch_odds(sport: str, session: aiohttp.ClientSession) -> list[dict]:
"""Fetch best available H2H odds for a sport."""
url = (
f"{ODDS_API_BASE}/sports/{sport}/odds"
f"?apiKey={ODDS_API_KEY}"
f"&regions=uk,eu"
f"&markets=h2h"
f"&oddsFormat=decimal"
f"&dateFormat=iso"
)
try:
async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
if resp.status == 200:
return await resp.json()
logger.warning("Odds API %s → %d", sport, resp.status)
except Exception as exc:
logger.error("fetch_odds error (%s): %s", sport, exc)
return []
# ── Analysis ──────────────────────────────────────────────────────────────────
def best_odds_for_outcome(bookmakers: list[dict], outcome_name: str) -> Optional[dict]:
"""Find the bookmaker offering the highest odds for a specific outcome."""
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
def analyse_event(event: dict) -> Optional[dict]:
"""
Analyse a single event and return a value tip if one exists,
or None if no edge is found.
"""
bookmakers = event.get("bookmakers", [])
if not bookmakers:
return None
home = event["home_team"]
away = event["away_team"]
outcomes = [home, away, "Draw"]
# Collect average odds across bookmakers per outcome
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
# Remove vig to get fair probs
raw_probs = [decimal_to_implied_prob(avg_odds[k]) for k in avg_odds]
fair_probs = remove_vig(raw_probs)
outcome_keys = list(avg_odds.keys())
best_tip = None
best_edge = MIN_VALUE_EDGE
for i, outcome in enumerate(outcome_keys):
# Get best available price in the market
best = best_odds_for_outcome(bookmakers, outcome)
if not best:
continue
edge = value_edge(fair_probs[i], best["price"])
if edge > best_edge:
best_edge = edge
conf = min(5, max(1, int(edge / 4)))
best_tip = {
"match": f"{home} vs {away}",
"league": event.get("sport_title", ""),
"date": _fmt_date(event.get("commence_time", "")),
"tip": f"{outcome} to win" if outcome not in ("Draw",) else "Draw",
"odds": round(best["price"], 2),
"bookmaker": best["bookmaker"],
"value_edge": round(edge, 1),
"confidence": conf,
"reasoning": (
f"Fair probability ~{round(fair_probs[i]*100,1)}% vs implied "
f"{round(decimal_to_implied_prob(best['price'])*100,1)}% "
f"at {best['bookmaker']}. Edge: +{round(edge,1)}%."
),
}
return best_tip
def _fmt_date(iso: str) -> str:
try:
dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
return dt.astimezone(timezone.utc).strftime("%d %b %Y %H:%M UTC")
except Exception:
return iso
# ── Public API ────────────────────────────────────────────────────────────────
async def get_tips() -> list[dict]:
"""Return today's best value tips, sorted by edge descending."""
all_tips = []
async with aiohttp.ClientSession() as session:
tasks = [fetch_odds(sport, session) for sport in SPORTS]
results = await asyncio.gather(*tasks)
for events in results:
for event in events:
tip = analyse_event(event)
if tip:
all_tips.append(tip)
# Sort by value edge, keep top N
all_tips.sort(key=lambda t: t["value_edge"], reverse=True)
return all_tips[:MAX_TIPS_PER_RUN]
async def get_daily_summary() -> str:
tips = await get_tips()
if not tips:
return " *Daily Summary*\n\nNo value bets found today. Markets look sharp — patienc
avg_edge = sum(t["value_edge"] for t in tips) / len(tips)
avg_odds = sum(t["odds"] for t in tips) / len(tips)
lines = [
f" *Daily Summary — {datetime.now().strftime('%d %b %Y')}*\n",
f" Value bets found: *{len(tips)}*",
f" Avg value edge: *+{round(avg_edge,1)}%*",
f" Avg best odds: *{round(avg_odds,2)}*\n",
"_Top picks today:_",
]
for i, t in enumerate(tips, 1):
lines.append(f"{i}. {t['match']} → {t['tip']} @ `{t['odds']}` (+{t['value_edge']}%)"
lines.append("\n return "\n".join(lines)
_Gamble responsibly. Past performance ≠ future results._")
