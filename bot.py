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

sent_today = {"date": "", "count": 0, "sent_ids": set(), "tips": []}


def reset_if_new_day():
    today = datetime.now(EST).strftime("%Y-%m-%d")
    if sent_today["date"] != today:
        sent_today["date"] = today
        sent_today["count"] = 0
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
        "----------------------",
        "Gamble responsibly. 18+",
    ]
    return "\n".join(lines)


def tip_id(tip):
    return tip["match"] + "|" + tip["tip"]


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("Today's Tips",    callback_data="tips")],
        [InlineKeyboardButton("Daily Summary",    callback_data="summary")],
        [InlineKeyboardButton("Today's Results",  callback_data="results")],
        [InlineKeyboardButton("How It Works",     callback_data="howto")],
    ]
    await update.message.reply_text(
        "Value Bet Bot\n\nI scan for high probability betting opportunities throughout the day. Max 12 tips per day with end of day results summary.\n\nUse the buttons below:",
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
        await msg.edit_text("No tips found right now. Try again later or use /diagnose.")
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
    text += "\nTips sent today: " + str(sent_today["count"]) + "/" + str(DAILY_TIP_LIMIT)
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
            await query.edit_message_text("No tips right now. Try again later.")
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
            "The bot scans bookmakers every 2 hours throughout the day (EST).\n"
            "When it finds a bet with 50-70% win probability it sends it to the channel.\n"
            "Maximum 12 tips are sent per day.\n\n"
            "At 11pm EST a results summary is posted showing WIN or LOSS for each tip.\n\n"
            "Always manage your bankroll responsibly."
        )


async def scan_and_broadcast(ctx: ContextTypes.DEFAULT_TYPE):
    reset_if_new_day()
    if sent_today["count"] >= DAILY_TIP_LIMIT:
        logger.info("Daily tip limit reached. Skipping scan.")
        return
    try:
        tips = await get_tips()
    except Exception as e:
        logger.error("Scan failed: %s", e)
        return
    if not tips:
        logger.info("Scan: no tips found.")
        return
    for tip in tips:
        if sent_today["count"] >= DAILY_TIP_LIMIT:
            break
        tid = tip_id(tip)
        if tid in sent_today["sent_ids"]:
            continue
        try:
            await ctx.bot.send_message(CHANNEL_ID, tip_card(tip))
            sent_today["sent_ids"].add(tid)
            sent_today["tips"].append(tip)
            sent_today["count"] += 1
            logger.info("Sent tip %d/%d: %s", sent_today["count"], DAILY_TIP_LIMIT, tid)
        except Exception as e:
            logger.error("Failed to send tip: %s", e)


async def post_results_summary(ctx: ContextTypes.DEFAULT_TYPE):
    reset_if_new_day()
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
    await update.message.reply_text("Pushing tips...")
    await scan_and_broadcast(ctx)
    await update.message.reply_text("Done. Tips sent today: " + str(sent_today["count"]) + "/" + str(DAILY_TIP_LIMIT))


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("tips",     tips_command))
    app.add_handler(CommandHandler("summary",  summary_command))
    app.add_handler(CommandHandler("results",  results_command))
    app.add_handler(CommandHandler("push",     push))
    app.add_handler(CommandHandler("diagnose", diagnose_command))
    app.add_handler(CallbackQueryHandler(button))

    # Scan times EST -> UTC
    # 02:00 EST = 07:00 UTC
    # 04:00 EST = 09:00 UTC
    # 06:00 EST = 11:00 UTC
    # 08:00 EST = 13:00 UTC
    # 10:00 EST = 15:00 UTC
    # 12:00 EST = 17:00 UTC
    # 14:00 EST = 19:00 UTC
    # 18:00 EST = 23:00 UTC
    scan_times_utc = [
        (7,  0),
        (9,  0),
        (11, 0),
        (13, 0),
        (15, 0),
        (17, 0),
        (19, 0),
        (23, 0),
    ]
    for h, m in scan_times_utc:
        app.job_queue.run_daily(scan_and_broadcast, time=dtime(hour=h, minute=m))

    # Results summary at 11:00 PM EST = 04:00 UTC next day
    app.job_queue.run_daily(post_results_summary, time=dtime(hour=4, minute=0))

    logger.info("Bot running. Scans at 02:00 04:00 06:00 08:00 10:00 12:00 14:00 18:00 EST")
    logger.info("Results summary at 23:00 EST")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
