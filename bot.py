import os
import logging
from datetime import datetime, time as dtime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes,
)
from tips_engine import get_tips, get_daily_summary, run_diagnostic
from results_engine import get_results, build_results_summary

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN  = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
ADMIN_ID   = int(os.environ.get("ADMIN_ID", "0").strip())

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set.")
if not CHANNEL_ID:
    raise RuntimeError("CHANNEL_ID environment variable is not set.")

DAILY_TIP_LIMIT = 12
EST = timezone(timedelta(hours=-5))

sent_today = {"date": "", "sent_ids": set(), "tips": []}
scheduled_tips = {}


def reset_if_new_day():
    today = datetime.now(EST).strftime("%Y-%m-%d")
    if sent_today["date"] != today:
        sent_today["date"] = today
        sent_today["sent_ids"] = set()
        sent_today["tips"] = []


def tip_card(tip):
    prob = str(tip.get("win_prob", "?")) + "%"
    bar_filled = int(tip.get("win_prob", 0) / 10)
    bar = "#" * bar_filled + "-" * (10 - bar_filled)
    lines = [
        "----------------------",
        tip["match"],
        tip["date"] + " | " + tip["league"],
        "----------------------",
        "Tip: " + tip["tip"],
        "Odds: " + str(tip["odds"]),
        "Win Probability: " + prob,
        "Confidence: [" + bar + "] " + prob,
        tip["reasoning"],
        "KICKOFF IN 1 HOUR",
        "----------------------",
        "Gamble responsibly. 18+",
    ]
    return "\n".join(lines)


def tip_id(tip):
    return tip["match"] + "|" + tip["tip"]


async def send_tip_job(ctx: ContextTypes.DEFAULT_TYPE):
    tip = ctx.job.data
    reset_if_new_day()
    tid = tip_id(tip)
    if tid in sent_today["sent_ids"]:
        return
    if len(sent_today["tips"]) >= DAILY_TIP_LIMIT:
        return
    try:
        await ctx.bot.send_message(CHANNEL_ID, tip_card(tip))
        sent_today["sent_ids"].add(tid)
        sent_today["tips"].append(tip)
        logger.info("Sent pre-game tip: %s", tid)
    except Exception as e:
        logger.error("Failed to send tip: %s", e)


async def schedule_tips(ctx: ContextTypes.DEFAULT_TYPE):
    reset_if_new_day()
    try:
        tips = await get_tips()
    except Exception as e:
        logger.error("Schedule scan failed: %s", e)
        return

    if not tips:
        logger.info("Schedule scan: no tips found.")
        return

    now = datetime.now(timezone.utc)
    scheduled_count = 0
    skipped_past = 0
    skipped_far = 0

    logger.info("Scan found %d tips. Now=%s UTC", len(tips), now.strftime("%H:%M"))

    for tip in tips:
        tid = tip_id(tip)
        if tid in sent_today["sent_ids"]:
            continue
        if tid in scheduled_tips:
            continue
        if len(sent_today["tips"]) + scheduled_count >= DAILY_TIP_LIMIT:
            break

        kickoff_str = tip.get("kickoff_utc", "")
        if not kickoff_str:
            logger.warning("No kickoff time for tip: %s", tid)
            continue

        try:
            kickoff = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
        except Exception as e:
            logger.error("Bad kickoff time %s: %s", kickoff_str, e)
            continue

        send_time = kickoff - timedelta(hours=1)

        if send_time <= now:
            skipped_past += 1
            logger.info("Skipping past tip: %s kickoff=%s", tid, kickoff.strftime("%H:%M UTC"))
            continue

        seconds_until = (send_time - now).total_seconds()

        if seconds_until > 48 * 3600:
            skipped_far += 1
            logger.info("Skipping far future tip: %s", tid)
            continue

        ctx.job_queue.run_once(
            send_tip_job,
            when=seconds_until,
            data=tip,
            name=tid,
        )
        scheduled_tips[tid] = send_time
        scheduled_count += 1
        logger.info(
            "Scheduled: %s at %s EST (in %.0f mins)",
            tid,
            send_time.astimezone(EST).strftime("%H:%M"),
            seconds_until / 60,
        )

    logger.info(
        "Scheduling done. Scheduled=%d, past=%d, far=%d",
        scheduled_count, skipped_past, skipped_far
    )


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("Today's Tips",    callback_data="tips")],
        [InlineKeyboardButton("Daily Summary",    callback_data="summary")],
        [InlineKeyboardButton("Today's Results",  callback_data="results")],
        [InlineKeyboardButton("How It Works",     callback_data="howto")],
    ]
    await update.message.reply_text(
        "Value Bet Bot\n\nTips are sent 1 hour before each game kicks off. Odds between 1.30 and 1.50. Max 12 tips per day.\n\nUse the buttons below:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def tips_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Fetching tips...")
    try:
        tips = await get_tips()
    except Exception as e:
        logger.error("get_tips failed: %s", e)
        await msg.edit_text("Error fetching tips: " + str(e))
        return
    if not tips:
        await msg.edit_text("No tips in the 1.30-1.50 range right now.")
        return
    await msg.delete()
    for tip in tips:
        await update.message.reply_text(tip_card(tip))


async def summary_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        text = await get_daily_summary()
    except Exception as e:
        text = "Error fetching summary: " + str(e)
    reset_if_new_day()
    text += "\nTips sent today: " + str(len(sent_today["tips"])) + "/" + str(DAILY_TIP_LIMIT)
    text += "\nTips scheduled: " + str(len(scheduled_tips))
    await update.message.reply_text(text)


async def results_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Checking results...")
    reset_if_new_day()
    if not sent_today["tips"]:
        await msg.edit_text("No tips have been sent today yet.")
        return
    try:
        tip_results = await get_results(sent_today["tips"])
        date_str = datetime.now(EST).strftime("%d %b %Y")
        summary = build_results_summary(tip_results, date_str)
        await msg.edit_text(summary)
    except Exception as e:
        await msg.edit_text("Error fetching results: " + str(e))


async def diagnose_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Running diagnostics...")
    try:
        report = await run_diagnostic()
    except Exception as e:
        await msg.edit_text("Diagnostic error: " + str(e))
        return
    await msg.edit_text(report)


async def scheduled_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    reset_if_new_day()
    if not scheduled_tips:
        await update.message.reply_text("No tips scheduled yet. Use /push to trigger a scan.")
        return
    lines = ["Scheduled Tips:", ""]
    for tid, send_time in scheduled_tips.items():
        lines.append(send_time.astimezone(EST).strftime("%H:%M EST") + " - " + tid)
    await update.message.reply_text("\n".join(lines))


async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "tips":
        try:
            tips = await get_tips()
        except Exception as e:
            await query.edit_message_text("Error fetching tips: " + str(e))
            return
        if not tips:
            await query.edit_message_text("No tips in the 1.30-1.50 range right now.")
            return
        await query.edit_message_text("Latest tips:")
        for tip in tips:
            await query.message.reply_text(tip_card(tip))
    elif query.data == "summary":
        text = await get_daily_summary()
        await query.edit_message_text(text)
    elif query.data == "results":
        reset_if_new_day()
        if not sent_today["tips"]:
            await query.edit_message_text("No tips have been sent today yet.")
            return
        try:
            tip_results = await get_results(sent_today["tips"])
            date_str = datetime.now(EST).strftime("%d %b %Y")
            summary = build_results_summary(tip_results, date_str)
            await query.edit_message_text(summary)
        except Exception as e:
            await query.edit_message_text("Error fetching results: " + str(e))
    elif query.data == "howto":
        await query.edit_message_text(
            "How It Works\n\n"
            "The bot scans for games every morning at 6am EST.\n"
            "Each tip is sent to the channel exactly 1 hour before kickoff.\n"
            "Odds filter: 1.30 to 1.50. Max 12 tips per day.\n\n"
            "At 11pm EST a results summary is posted showing WIN or LOSS for each tip.\n\n"
            "Always manage your bankroll responsibly."
        )


async def post_results_summary(ctx: ContextTypes.DEFAULT_TYPE):
    reset_if_new_day()
    scheduled_tips.clear()
    if not sent_today["tips"]:
        logger.info("Results summary: no tips sent today.")
        return
    try:
        tip_results = await get_results(sent_today["tips"])
        date_str = datetime.now(EST).strftime("%d %b %Y")
        summary = build_results_summary(tip_results, date_str)
        await ctx.bot.send_message(CHANNEL_ID, summary)
        logger.info("Results summary posted.")
    except Exception as e:
        logger.error("Failed to post results summary: %s", e)


async def push(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Admin only.")
        return
    await update.message.reply_text("Scanning and scheduling tips...")
    await schedule_tips(ctx)
    await update.message.reply_text(
        "Done. Scheduled: " + str(len(scheduled_tips)) + " tips. "
        + "Sent today: " + str(len(sent_today["tips"])) + "/" + str(DAILY_TIP_LIMIT)
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("tips",      tips_command))
    app.add_handler(CommandHandler("summary",   summary_command))
    app.add_handler(CommandHandler("results",   results_command))
    app.add_handler(CommandHandler("push",      push))
    app.add_handler(CommandHandler("diagnose",  diagnose_command))
    app.add_handler(CommandHandler("scheduled", scheduled_command))
    app.add_handler(CallbackQueryHandler(button))

    # Scan at 6am EST = 11:00 UTC
    app.job_queue.run_daily(schedule_tips, time=dtime(hour=11, minute=0))

    # Results summary at 11pm EST = 04:00 UTC
    app.job_queue.run_daily(post_results_summary, time=dtime(hour=4, minute=0))

    logger.info("Bot running. 1 scan at 6am EST. Tips sent 1 hour before kickoff.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
