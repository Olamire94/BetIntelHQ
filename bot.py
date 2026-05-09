"""
bot.py — BetIntelHQ Telegram Bot

Schedule (all EST):
  06:00 → scan all sports (10 API calls), store best tip
  1 h before kickoff → send tip to channel
  23:00 → post daily results summary
"""

import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Bot
from telegram.ext import ApplicationBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from tips_engine import get_best_tip

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config  (Railway environment variables)
# ---------------------------------------------------------------------------

BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
EST        = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

state = {
    "tip":         None,   # dict | None — today's tip
    "tip_sent":    False,  # has the tip message been posted?
    "send_job":    None,   # APScheduler job for the timed send
}

# ---------------------------------------------------------------------------
# Sport labels
# ---------------------------------------------------------------------------

SPORT_LABELS = {
    "soccer_epl":                "⚽ EPL",
    "soccer_spain_la_liga":      "⚽ La Liga",
    "soccer_germany_bundesliga": "⚽ Bundesliga",
    "soccer_italy_serie_a":      "⚽ Serie A",
    "soccer_uefa_champs_league": "⚽ Champions League",
    "soccer_australia_aleague":  "⚽ A-League",
    "soccer_japan_j_league":     "⚽ J-League",
    "basketball_nba":            "🏀 NBA",
    "americanfootball_nfl":      "🏈 NFL",
    "baseball_mlb":              "⚾ MLB",
}


def sport_label(sport: str) -> str:
    return SPORT_LABELS.get(sport, sport)


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def build_tip_message(tip: dict) -> str:
    kickoff_str = tip["kickoff_est"].strftime("%I:%M %p EST")
    return (
        f"🎯 *BetIntelHQ — Daily Tip*\n\n"
        f"{sport_label(tip['sport'])}\n"
        f"📋 *Match:* {tip['home_team']} vs {tip['away_team']}\n"
        f"✅ *Pick:* {tip['pick']}\n"
        f"💰 *Odds:* {tip['odds']:.2f}\n"
        f"📚 *Book:* {tip['bookmaker']}\n"
        f"⏰ *Kickoff:* {kickoff_str}\n\n"
        f"_Stake responsibly. 1 unit max._"
    )


def build_no_tip_message() -> str:
    return (
        "🔍 *BetIntelHQ — Daily Scan*\n\n"
        "No qualifying tip found today (odds band 1.30–1.50). "
        "Rest day — sit this one out. 🧘"
    )


def build_summary_message() -> str:
    date_str = datetime.now(EST).strftime("%A, %B %d %Y")
    tip = state["tip"]

    if not tip or not state["tip_sent"]:
        return (
            f"📊 *BetIntelHQ — Daily Summary ({date_str})*\n\n"
            "No tip was issued today."
        )

    kickoff_str = tip["kickoff_est"].strftime("%I:%M %p EST")
    return (
        f"📊 *BetIntelHQ — Daily Summary ({date_str})*\n\n"
        f"{sport_label(tip['sport'])}\n"
        f"📋 {tip['home_team']} vs {tip['away_team']}\n"
        f"✅ Pick: *{tip['pick']}* @ {tip['odds']:.2f}\n"
        f"⏰ Kickoff: {kickoff_str}\n\n"
        f"_Results are not tracked automatically. "
        f"Check your bookmaker for the final outcome._"
    )


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

async def job_daily_scan(scheduler: AsyncIOScheduler) -> None:
    """06:00 EST — scan all sports, schedule the tip send 1 h before kickoff."""
    logger.info("Running daily scan...")

    # Reset daily state
    state["tip"]      = None
    state["tip_sent"] = False
    if state["send_job"]:
        try:
            state["send_job"].remove()
        except Exception:
            pass
        state["send_job"] = None

    tip = get_best_tip()
    state["tip"] = tip

    bot = Bot(token=BOT_TOKEN)

    if not tip:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=build_no_tip_message(),
            parse_mode="Markdown",
        )
        logger.info("No-tip message sent.")
        return

    # Schedule the tip to fire 1 hour before kickoff
    send_at = tip["kickoff_est"] - timedelta(hours=1)
    now_est  = datetime.now(EST)

    if send_at <= now_est:
        # Kickoff is within the hour — send immediately
        logger.warning(
            "Kickoff %s is within 1 h of scan time; sending tip now.",
            tip["kickoff_est"].strftime("%H:%M EST"),
        )
        await send_tip()
    else:
        job = scheduler.add_job(
            send_tip,
            trigger="date",
            run_date=send_at,
            id="send_tip",
            replace_existing=True,
        )
        state["send_job"] = job
        logger.info(
            "Tip queued — will be sent at %s EST",
            send_at.strftime("%H:%M"),
        )


async def send_tip() -> None:
    """Send the stored tip to the channel."""
    tip = state["tip"]
    if not tip:
        logger.warning("send_tip called but no tip in state.")
        return

    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=build_tip_message(tip),
        parse_mode="Markdown",
    )
    state["tip_sent"] = True
    logger.info("Tip sent: %s @ %.2f", tip["pick"], tip["odds"])


async def job_results_summary() -> None:
    """23:00 EST — post the daily results summary."""
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=build_summary_message(),
        parse_mode="Markdown",
    )
    logger.info("Results summary posted.")


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

def build_scheduler(app) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=EST)

    scheduler.add_job(
        job_daily_scan,
        trigger="cron",
        hour=6, minute=0,
        kwargs={"scheduler": scheduler},
        id="daily_scan",
        replace_existing=True,
    )
    scheduler.add_job(
        job_results_summary,
        trigger="cron",
        hour=23, minute=0,
        id="results_summary",
        replace_existing=True,
    )

    return scheduler


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    scheduler = build_scheduler(app)
    scheduler.start()
    logger.info("BetIntelHQ bot started. Waiting for 06:00 EST scan.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
