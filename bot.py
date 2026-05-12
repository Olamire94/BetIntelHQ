"""
bot.py — BetIntelHQ Telegram Bot

Schedule (all Atlantic / Halifax time):
  06:00 AT → daily scan (10 API calls), store best tip
  KO - 1h  → send tip to channel
  23:00 AT → post daily results summary
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
# Config  (set as Railway environment variables)
# ---------------------------------------------------------------------------

BOT_TOKEN  = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
ATL        = ZoneInfo("America/Halifax")

# ---------------------------------------------------------------------------
# In-memory state  (resets on redeploy — fine for a daily bot)
# ---------------------------------------------------------------------------

state: dict = {
    "tip":      None,   # today's best tip dict | None
    "tip_sent": False,  # True once the signal message is posted
    "send_job": None,   # APScheduler one-shot job reference
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
# Confidence bar  e.g. 73.5% → "███████░░░"
# ---------------------------------------------------------------------------

def confidence_bar(pct: float, width: int = 10) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)

# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def build_tip_message(tip: dict) -> str:
    ko_str  = tip["kickoff_atl"].strftime("%I:%M %p AT")
    bar     = confidence_bar(tip["confidence"])
    conf    = tip["confidence"]
    return (
        f"🎯 *BetIntelHQ — Daily Signal*\n\n"
        f"{sport_label(tip['sport'])}\n"
        f"📋 *Match:* {tip['home_team']} vs {tip['away_team']}\n"
        f"✅ *Pick:* {tip['pick']}\n"
        f"💰 *Odds:* {tip['odds']:.2f}\n"
        f"📚 *Best book:* {tip['bookmaker']} "
        f"({tip['book_count']} books checked)\n"
        f"📊 *Confidence:* {bar} {conf}%\n"
        f"⏰ *Kickoff:* {ko_str}\n\n"
        f"_Stake responsibly. 1 unit max._"
    )


def build_no_tip_message() -> str:
    date_str = datetime.now(ATL).strftime("%A, %B %d")
    return (
        f"🔍 *BetIntelHQ — {date_str}*\n\n"
        f"Scan complete. No tip meets today's confidence threshold "
        f"(odds 1.30–1.50, multi-book agreement required).\n\n"
        f"Rest day — protect the bankroll. 🧘"
    )


def build_summary_message() -> str:
    date_str = datetime.now(ATL).strftime("%A, %B %d %Y")
    tip = state["tip"]

    if not tip or not state["tip_sent"]:
        return (
            f"📊 *BetIntelHQ — Summary ({date_str})*\n\n"
            "No signal was issued today."
        )

    ko_str = tip["kickoff_atl"].strftime("%I:%M %p AT")
    bar    = confidence_bar(tip["confidence"])
    return (
        f"📊 *BetIntelHQ — Summary ({date_str})*\n\n"
        f"{sport_label(tip['sport'])}\n"
        f"📋 {tip['home_team']} vs {tip['away_team']}\n"
        f"✅ Pick: *{tip['pick']}* @ {tip['odds']:.2f}\n"
        f"📊 Confidence: {bar} {tip['confidence']}%\n"
        f"⏰ Kickoff was: {ko_str}\n\n"
        f"_Check your bookmaker for the result. "
        f"Track your units and stay disciplined._"
    )

# ---------------------------------------------------------------------------
# Core jobs
# ---------------------------------------------------------------------------

async def job_daily_scan(scheduler: AsyncIOScheduler) -> None:
    """06:00 AT — run the full scan and queue the tip send."""
    logger.info("=== Daily scan starting ===")

    # Reset state
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
        logger.info("No qualifying tip — rest day message sent.")
        return

    # Queue send for KO - 1 hour
    send_at  = tip["kickoff_atl"] - timedelta(hours=1)
    now_atl  = datetime.now(ATL)

    if send_at <= now_atl:
        # Already within 1 h of kickoff — send immediately
        logger.warning(
            "Kickoff %s is within 1 h of scan — sending tip now.",
            tip["kickoff_atl"].strftime("%I:%M %p AT"),
        )
        await _send_tip(bot)
    else:
        job = scheduler.add_job(
            _send_tip_scheduled,
            trigger="date",
            run_date=send_at,
            id="send_tip",
            replace_existing=True,
        )
        state["send_job"] = job
        logger.info(
            "Tip queued: will send at %s AT  (KO %s AT)",
            send_at.strftime("%I:%M %p"),
            tip["kickoff_atl"].strftime("%I:%M %p"),
        )


async def _send_tip(bot: Bot) -> None:
    """Post the tip message. Shared by immediate and scheduled paths."""
    tip = state["tip"]
    if not tip:
        logger.warning("_send_tip called with no tip in state.")
        return

    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=build_tip_message(tip),
        parse_mode="Markdown",
    )
    state["tip_sent"] = True
    logger.info("Signal sent: %s @ %.2f (confidence %.1f%%)",
                tip["pick"], tip["odds"], tip["confidence"])


async def _send_tip_scheduled() -> None:
    """APScheduler async entry point for the queued send."""
    bot = Bot(token=BOT_TOKEN)
    await _send_tip(bot)


async def job_results_summary() -> None:
    """23:00 AT — post the day's summary."""
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=build_summary_message(),
        parse_mode="Markdown",
    )
    logger.info("Results summary posted.")

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ATL)

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


async def post_init(app) -> None:
    """Called by python-telegram-bot after the event loop is running."""
    scheduler = build_scheduler()
    scheduler.start()
    app.bot_data["scheduler"] = scheduler
    logger.info("BetIntelHQ live — next scan at 06:00 AT (Halifax)")


def main() -> None:
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
