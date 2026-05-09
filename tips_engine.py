import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
import aiohttp
logger = logging.getLogger(__name__)
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "").strip()
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
EST = timezone(timedelta(hours=-5))
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
SOCCER_SPORTS = [
"soccer_epl",
"soccer_spain_la_liga",
"soccer_germany_bundesliga",
"soccer_italy_serie_a",
"soccer_uefa_champs_league",
"soccer_australia_aleague",
"soccer_japan_j_league",
]
MIN_ODDS = 1.30
MAX_ODDS = 1.50
def decimal_to_implied_prob(odds):
return 1.0 / odds if odds > 0 else 0.0
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
except Exception as e:
logger.error("fetch_odds error (%s): %s", sport, e)
return [], sport
def fmt_date(iso):
try:
dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
return dt.astimezone(EST).strftime("%d %b %Y %H:%M EST")
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
return []
home = event["home_team"]
away = event["away_team"]
outcome_map = get_all_outcomes(bookmakers)
if not outcome_map:
return []
tips = []
for label, prices in outcome_map.items():
if not prices:
continue
best_price = max(prices)
avg_price = sum(prices) / len(prices)
if best_price < MIN_ODDS or best_price > MAX_ODDS:
continue
win_prob = round(decimal_to_implied_prob(avg_price) * 100, 1)
implied_at_best = round(decimal_to_implied_prob(best_price) * 100, 1)
reasoning = (
"Market consensus probability: " + str(win_prob) + "%."
+ " Best odds imply: " + str(implied_at_best) + "%."
)
tips.append({
"match": home + " vs " + away,
"league": event.get("sport_title", ""),
"date": fmt_date(event.get("commence_time", "")),
"kickoff_utc": event.get("commence_time", ""),
"tip": label,
"odds": round(best_price, 2),
"win_prob": win_prob,
"confidence": min(5, max(1, int(win_prob / 20))),
"reasoning": reasoning,
})
tips.sort(key=lambda t: t["win_prob"], reverse=True)
return tips[:1]
async def get_best_tip(window_start=0, window_end=24):
all_tips = []
async with aiohttp.ClientSession() as session:
tasks = [fetch_odds(sport, session) for sport in SPORTS]
results = await asyncio.gather(*tasks)
for result in results:
events, sport = result
for event in events:
kickoff_str = event.get("commence_time", "")
try:
kickoff = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
hour = kickoff.astimezone(EST).hour
if hour < window_start or hour >= window_end:
continue
except Exception:
pass
tips = analyse_event(event)
all_tips.extend(tips)
all_tips.sort(key=lambda t: t["win_prob"], reverse=True)
return all_tips[0] if all_tips else None
async def get_tips():
all_tips = []
async with aiohttp.ClientSession() as session:
tasks = [fetch_odds(sport, session) for sport in SPORTS]
results = await asyncio.gather(*tasks)
for result in results:
events, sport = result
for event in events:
tips = analyse_event(event)
all_tips.extend(tips)
all_tips.sort(key=lambda t: t["win_prob"], reverse=True)
return all_tips[:5]
async def get_daily_summary():
tip = await get_best_tip()
if not tip:
return "\n".join([
"Daily Summary",
"",
"No tips in the 1.30-1.50 range today.",
"Try /diagnose to check the API.",
])
return "\n".join([
"",
"Best tip today:",
"Daily Summary - " + datetime.now(EST).strftime("%d %b %Y"),
tip["match"] + " - " + tip["tip"],
"Odds: " + str(tip["odds"]) + " | Win prob: " + str(tip["win_prob"]) + "%",
"",
"Gamble responsibly.",
])
async def run_diagnostic():
lines = ["Diagnostic Report", ""]
if not ODDS_API_KEY:
lines.append("ERROR: ODDS_API_KEY is not set.")
return "\n".join(lines)
lines.append("API Key: set (" + ODDS_API_KEY[:6] + "...)")
lines.append("Odds range: " + str(MIN_ODDS) + " - " + str(MAX_ODDS))
lines.append("")
async with aiohttp.ClientSession() as session:
for sport in SPORTS:
try:
events, _ = await fetch_odds(sport, session)
count = len(events)
tips_found = sum(len(analyse_event(e)) for e in events)
lines.append(sport + ": " + str(count) + " games, " + str(tips_found) + " tip
except Exception as e:
lines.append(sport + ": ERROR - " + str(e))
lines.append("")
lines.append("Odds filter: " + str(MIN_ODDS) + " to " + str(MAX_ODDS))
return "\n".join(lines)
