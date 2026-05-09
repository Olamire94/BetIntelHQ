import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
import aiohttp

logger = logging.getLogger(**name**)

ODDS_API_KEY  = os.environ.get(“ODDS_API_KEY”, “”).strip()
ODDS_API_BASE = “https://api.the-odds-api.com/v4”
EST = timezone(timedelta(hours=-5))

SPORTS = [
“soccer_epl”,
“soccer_spain_la_liga”,
“soccer_germany_bundesliga”,
“soccer_italy_serie_a”,
“soccer_uefa_champs_league”,
“soccer_australia_aleague”,
“soccer_japan_j_league”,
“basketball_nba”,
“americanfootball_nfl”,
“baseball_mlb”,
]

SOCCER_SPORTS = [
“soccer_epl”,
“soccer_spain_la_liga”,
“soccer_germany_bundesliga”,
“soccer_italy_serie_a”,
“soccer_uefa_champs_league”,
“soccer_australia_aleague”,
“soccer_japan_j_league”,
]

MIN_ODDS = 1.30
MAX_ODDS = 1.50
MAX_TIPS_PER_RUN = 10

def decimal_to_implied_prob(odds):
return 1.0 / odds if odds > 0 else 0.0

def value_edge(fair_prob, decimal_odds):
return (fair_prob * decimal_odds - 1) * 100

async def fetch_odds(sport, session):
is_soccer = sport in SOCCER_SPORTS
markets = “h2h,totals,spreads”
if is_soccer:
markets = “h2h,totals,btts,spreads”

```
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
            data = await resp.json()
            return data, sport
        logger.warning("Odds API %s returned %d", sport, resp.status)
        return [], sport
except Exception as exc:
    logger.error("fetch_odds error (%s): %s", sport, exc)
return [], sport
```

def fmt_date(iso):
try:
dt = datetime.fromisoformat(iso.replace(“Z”, “+00:00”))
return dt.astimezone(EST).strftime(”%d %b %Y %H:%M EST”)
except Exception:
return iso

def get_all_outcomes(bookmakers):
outcome_map = {}
for bm in bookmakers:
for market in bm.get(“markets”, []):
mkey = market.get(“key”)
for o in market.get(“outcomes”, []):
name = o[“name”]
price = o[“price”]
point = o.get(“point”, None)

```
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
```

def analyse_event(event):
bookmakers = event.get(“bookmakers”, [])
if not bookmakers:
return []

```
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
        "match":      home + " vs " + away,
        "league":     event.get("sport_title", ""),
        "date":       fmt_date(event.get("commence_time", "")),
        "tip":        label,
        "odds":       round(best_price, 2),
        "win_prob":   win_prob,
        "confidence": min(5, max(1, int(win_prob / 20))),
        "reasoning":  reasoning,
    })

tips.sort(key=lambda t: t["win_prob"], reverse=True)
return tips[:1]
```

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
all_tips.sort(key=lambda t: t[“win_prob”], reverse=True)
return all_tips[:MAX_TIPS_PER_RUN]

async def run_diagnostic():
lines = [“Diagnostic Report”, “”]
if not ODDS_API_KEY:
lines.append(“ERROR: ODDS_API_KEY is not set.”)
return “\n”.join(lines)

```
lines.append("API Key: set (" + ODDS_API_KEY[:6] + "...)")
lines.append("Odds range: " + str(MIN_ODDS) + " - " + str(MAX_ODDS))
lines.append("")

async with aiohttp.ClientSession() as session:
    for sport in SPORTS:
        try:
            events, _ = await fetch_odds(sport, session)
            count = len(events)
            tips_found = 0
            for event in events:
                tips = analyse_event(event)
                tips_found += len(tips)
            lines.append(sport + ": " + str(count) + " games, " + str(tips_found) + " tips")
        except Exception as e:
            lines.append(sport + ": ERROR - " + str(e))

lines.append("")
lines.append("Odds filter: " + str(MIN_ODDS) + " to " + str(MAX_ODDS))
return "\n".join(lines)
```

async def get_daily_summary():
tips = await get_tips()
if not tips:
lines = [
“Daily Summary”,
“”,
“No tips in the “ + str(MIN_ODDS) + “-” + str(MAX_ODDS) + “ odds range right now.”,
“Try /diagnose to check what data is available.”,
]
return “\n”.join(lines)

```
avg_prob = sum(t["win_prob"] for t in tips) / len(tips)
avg_odds = sum(t["odds"] for t in tips) / len(tips)

lines = [
    "Daily Summary - " + datetime.now(EST).strftime("%d %b %Y"),
    "",
    "Tips found: " + str(len(tips)),
    "Avg win probability: " + str(round(avg_prob, 1)) + "%",
    "Avg odds: " + str(round(avg_odds, 2)),
    "",
    "Current picks:",
]
for i, t in enumerate(tips, 1):
    lines.append(
        str(i) + ". " + t["match"] + " - " + t["tip"]
        + " @ " + str(t["odds"]) + " (" + str(t["win_prob"]) + "% win prob)"
    )
lines.append("")
lines.append("Gamble responsibly.")
return "\n".join(lines)
```
