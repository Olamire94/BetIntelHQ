import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
import aiohttp

logger = logging.getLogger(__name__)

ODDS_API_KEY  = os.environ.get("ODDS_API_KEY", "").strip()
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
EST = timezone(timedelta(hours=-5))

SPORTS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_uefa_champs_league",
    "soccer_france_ligue_one",
    "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga",
    "soccer_turkey_super_league",
    "soccer_brazil_campeonato",
    "soccer_argentina_primera_division",
    "soccer_mexico_ligamx",
    "soccer_usa_mls",
    "soccer_uefa_europa_league",
    "soccer_england_efl_champ",
    "soccer_australia_aleague",
    "soccer_japan_j_league",
    "basketball_nba",
    "americanfootball_nfl",
    "baseball_mlb",
]


async def fetch_scores(sport, session):
    url = (
        ODDS_API_BASE + "/sports/" + sport + "/scores"
        + "?apiKey=" + ODDS_API_KEY
        + "&daysFrom=1"
        + "&dateFormat=iso"
    )
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                return await resp.json()
            logger.warning("Scores API %s returned %d", sport, resp.status)
    except Exception as exc:
        logger.error("fetch_scores error (%s): %s", sport, exc)
    return []


def get_score(game):
    scores = game.get("scores")
    if not scores:
        return None, None
    result = {}
    for s in scores:
        result[s["name"]] = s.get("score", "?")
    return result


def did_tip_win(tip, game):
    tip_text = tip["tip"].lower()
    home = game["home_team"]
    away = game["away_team"]
    scores = game.get("scores")

    if not scores:
        return None

    score_map = {}
    for s in scores:
        try:
            score_map[s["name"]] = float(s.get("score", 0))
        except Exception:
            score_map[s["name"]] = 0.0

    home_score = score_map.get(home, 0)
    away_score = score_map.get(away, 0)
    total = home_score + away_score

    if "to win" in tip_text:
        team = tip_text.replace(" to win", "").strip()
        if team.lower() == home.lower():
            return home_score > away_score
        elif team.lower() == away.lower():
            return away_score > home_score
        else:
            return None

    if "draw" in tip_text:
        return home_score == away_score

    if "over" in tip_text:
        try:
            line = float(tip_text.split("over")[1].split("goals")[0].split("runs")[0].strip())
            return total > line
        except Exception:
            return None

    if "under" in tip_text:
        try:
            line = float(tip_text.split("under")[1].split("goals")[0].split("runs")[0].strip())
            return total < line
        except Exception:
            return None

    if "btts yes" in tip_text:
        return home_score > 0 and away_score > 0

    if "btts no" in tip_text:
        return home_score == 0 or away_score == 0

    if "handicap" in tip_text:
        try:
            parts = tip_text.split("handicap")
            team = parts[0].strip()
            line = float(parts[1].strip().split(" ")[0])
            if team.lower() == home.lower():
                return (home_score + line) > away_score
            elif team.lower() == away.lower():
                return (away_score + line) > home_score
        except Exception:
            return None

    return None


async def get_results(sent_tips):
    if not sent_tips:
        return []

    all_scores = []
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_scores(sport, session) for sport in SPORTS]
        results = await asyncio.gather(*tasks)
    for games in results:
        all_scores.extend(games)

    completed = [g for g in all_scores if g.get("completed") is True]

    tip_results = []
    for tip in sent_tips:
        matched_game = None
        tip_match = tip["match"].lower()
        for game in completed:
            game_match = (game["home_team"] + " vs " + game["away_team"]).lower()
            if (game["home_team"].lower() in tip_match and
                    game["away_team"].lower() in tip_match):
                matched_game = game
                break

        if not matched_game:
            tip_results.append({
                "tip":    tip,
                "result": "pending",
                "score":  "N/A",
            })
            continue

        score_map = {}
        for s in matched_game.get("scores") or []:
            score_map[s["name"]] = s.get("score", "?")

        home = matched_game["home_team"]
        away = matched_game["away_team"]
        score_str = (
            home + " " + str(score_map.get(home, "?"))
            + " - " + str(score_map.get(away, "?"))
            + " " + away
        )

        won = did_tip_win(tip, matched_game)
        if won is True:
            result = "WIN"
        elif won is False:
            result = "LOSS"
        else:
            result = "pending"

        tip_results.append({
            "tip":    tip,
            "result": result,
            "score":  score_str,
        })

    return tip_results


def build_results_summary(tip_results, date_str):
    wins   = [r for r in tip_results if r["result"] == "WIN"]
    losses = [r for r in tip_results if r["result"] == "LOSS"]
    pending = [r for r in tip_results if r["result"] == "pending"]

    lines = [
        "Results Summary - " + date_str,
        "",
        "Record: " + str(len(wins)) + "W / " + str(len(losses)) + "L / " + str(len(pending)) + " pending",
        "",
    ]

    for r in tip_results:
        tip = r["tip"]
        icon = "WIN" if r["result"] == "WIN" else ("LOSS" if r["result"] == "LOSS" else "PENDING")
        lines.append(icon + " | " + tip["match"])
        lines.append("     Tip: " + tip["tip"] + " @ " + str(tip["odds"]))
        lines.append("     Score: " + r["score"])
        lines.append("")

    lines.append("Gamble responsibly.")
    return "\n".join(lines)
