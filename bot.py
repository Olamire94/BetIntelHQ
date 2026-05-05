import os
import logging
from datetime import datetime, time as dtime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes,
)
from tips_engine import get_tips, get_daily_summary, run_diagnostic

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN  = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
ADMIN_ID   = int(os.environ.get("ADMIN_ID", "0").strip())

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set.")
if not CHANNEL_ID:
    raise RuntimeError("CHANNEL_ID environment variable is not set.")


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


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("Today's Tips",  callback_data="tips")],
        [InlineKeyboardButton("Daily Summary",  callback_data="summary")],
        [InlineKeyboardButton("How It Works",   callback_data="howto")],
    ]
    await update.message.reply_text(
        "Value Bet Bot\n\nI find bets with the highest probability of winning across football, NBA, NFL and MLB.\n\nUse the buttons below:",
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
        await msg.edit_text("No tips found right now. Try /diagnose to check the API connection.")
        return
    await msg.delete()
    for tip in tips:
        await update.message.reply_text(tip_card(tip))


async def summary_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        text = await get_daily_summary()
    except Exception as e:
        logger.error("summary failed: %s", e)
        text = "Error fetching summary: " + str(e)
    await update.message.reply_text(text)


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
            await query.edit_message_text("No tips found right now. Try /diagnose to check the connection.")
            return
        await query.edit_message_text("Here are today's top tips:")
        for tip in tips:
            await query.message.reply_text(tip_card(tip))
    elif query.data == "summary":
        text = await get_daily_summary()
        await query.edit_message_text(text)
    elif query.data == "howto":
        await query.edit_message_text(
            "How It Works\n\n"
            "Bookmakers build a profit margin into every odds price.\n"
            "Our model strips that margin out to find the true probability of each outcome.\n\n"
            "Example:\n"
            "Bookie odds: 2.50 implies a 40% chance\n"
            "Our model says: true chance is 55%\n"
            "Result: a high probability tip\n\n"
            "We only show tips where our model gives 50% or higher win probability.\n\n"
            "Always manage your bankroll responsibly."
        )


async def broadcast_tips(ctx: ContextTypes.DEFAULT_TYPE):
    try:
        tips = await get_tips()
    except Exception as e:
        logger.error("Broadcast failed: %s", e)
        return
    if not tips:
        logger.info("Broadcast: no tips today.")
        return
    header = "Daily Tips - " + datetime.now().strftime("%d %b %Y") + "\nTop " + str(len(tips)) + " picks today\n"
    try:
        await ctx.bot.send_message(CHANNEL_ID, header)
        for tip in tips:
            await ctx.bot.send_message(CHANNEL_ID, tip_card(tip))
        logger.info("Broadcast: %d tips sent.", len(tips))
    except Exception as e:
        logger.error("Channel send failed: %s", e)


async def push(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Admin only.")
        return
    await update.message.reply_text("Pushing tips...")
    await broadcast_tips(ctx)
    await update.message.reply_text("Done.")


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("tips",    tips_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("push",    push))
    app.add_handler(CommandHandler("diagnose", diagnose_command))
    app.add_handler(CallbackQueryHandler(button))
    app.job_queue.run_daily(broadcast_tips, time=dtime(hour=8, minute=0))
    logger.info("Bot running.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
