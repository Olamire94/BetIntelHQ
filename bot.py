import os
import logging
from datetime import datetime, time as dtime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes,
)
from tips_engine import get_tips, get_daily_summary

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
    stars = "STAR" * tip.get("confidence", 3)
    arrow = "UP" if tip.get("value_edge", 0) >= 10 else "RIGHT"
    return (
        "----------------------\n"
        + "*" + tip["match"] + "*\n"
        + tip["date"] + " | " + tip["league"] + "\n"
        + "----------------------\n"
        + "Tip: " + tip["tip"] + "\n"
        + "Odds: " + str(tip["odds"]) + "\n"
        + "Value Edge: +" + str(tip["value_edge"]) + "%\n"
        + "Confidence: " + stars + "\n"
        + tip["reasoning"] + "\n"
        + "----------------------\n"
        + "Gamble responsibly. 18+"
    )

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("Today's Tips",  callback_data="tips")],
        [InlineKeyboardButton("Daily Summary",  callback_data="summary")],
        [InlineKeyboardButton("How It Works",   callback_data="howto")],
    ]
    await update.message.reply_text(
        "Value Bet Bot\n\nI scan bookmakers for bets where the true probability beats the implied odds.\n\nUse the buttons below:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def tips_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Fetching value tips...")
    try:
        tips = await get_tips()
    except Exception as e:
        logger.error("get_tips failed: %s", e)
        await msg.edit_text("Error fetching tips. Check your ODDS_API_KEY.")
        return
    if not tips:
        await msg.edit_text("No strong value bets found right now. Check back later!")
        return
    await msg.delete()
    for tip in tips:
        await update.message.reply_text(tip_card(tip))

async def summary_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        text = await get_daily_summary()
    except Exception as e:
        logger.error("summary failed: %s", e)
        text = "Error fetching summary. Check your ODDS_API_KEY."
    await update.message.reply_text(text)

async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "tips":
        try:
            tips = await get_tips()
        except Exception:
            await query.edit_message_text("Error fetching tips. Check your ODDS_API_KEY.")
            return
        if not tips:
            await query.edit_message_text("No strong value bets right now.")
            return
        await query.edit_message_text("Here are today's value bets:")
        for tip in tips:
            await query.message.reply_text(tip_card(tip))
    elif query.data == "summary":
        text = await get_daily_summary()
        await query.edit_message_text(text)
    elif query.data == "howto":
        await query.edit_message_text(
            "How Value Betting Works\n\n"
            "Bookmakers set odds including their profit margin.\n"
            "A value bet exists when our model finds the true probability is higher than the odds imply.\n\n"
            "Example:\n"
            "Bookie odds: 2.50 means implied 40%\n"
            "Our model: true prob 50%\n"
            "Edge = +10%\n\n"
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
    header = "Daily Value Tips - " + datetime.now().strftime("%d %b %Y") + "\nFound " + str(len(tips)) + " value bet(s) today\n"
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
    app.add_handler(CallbackQueryHandler(button))
    app.job_queue.run_daily(broadcast_tips, time=dtime(hour=8, minute=0))
    logger.info("Bot running.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
