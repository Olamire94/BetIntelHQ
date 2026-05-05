import os
import logging
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
Application, CommandHandler, CallbackQueryHandler, ContextTypes, JobQueue
)
from tips_engine import get_tips, get_daily_summary
# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
format="%(asctime)s | %(levelname)s | %(message)s",
level=logging.INFO,
)
logger = logging.getLogger(__name__)
# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "YOUR_CHANNEL_ID_HERE") # e.g. @mybettingchannel
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0")) # your Telegram user ID
# ── Helpers ───────────────────────────────────────────────────────────────────
def tip_card(tip: dict) -> str:
"""Format a single tip as a Telegram message."""
stars = " " * tip.get("confidence", 3)
arrow = " " if tip.get("value_edge", 0) >= 10 else " "
return (
f"━━━━━━━━━━━━━━━━━━━━━━\n"
f" *{tip['match']}*\n"
f" {tip['date']} | {tip['league']}\n"
f"━━━━━━━━━━━━━━━━━━━━━━\n"
f" *Tip:* {tip['tip']}\n"
f" *Odds:* `{tip['odds']}`\n"
f"{arrow} *Value Edge:* +{tip['value_edge']}%\n"
f" *Confidence:* {stars}\n"
f" {tip['reasoning']}\n"
f"━━━━━━━━━━━━━━━━━━━━━━\n"
f" _Gamble responsibly. 18+_"
)
# ── Command Handlers ──────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
kb = [
[InlineKeyboardButton(" Today's Tips", callback_data="tips")],
[InlineKeyboardButton(" Daily Summary", callback_data="summary")],
[InlineKeyboardButton(" How It Works", callback_data="howto")],
]
await update.message.reply_text(
" *Value Bet Bot*\n\n"
"I analyse odds from major bookmakers and surface bets where the _true probability_ "
"is higher than the implied odds suggest — giving you a mathematical edge.\n\n"
"Use the buttons below to get started:",
parse_mode="Markdown",
reply_markup=InlineKeyboardMarkup(kb),
)
async def tips_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
msg = await update.message.reply_text(" Fetching value tips…")
tips = await get_tips()
if not tips:
await msg.edit_text(" No strong value bets found right now. Check back later!")
return
for tip in tips:
await msg.delete()
await update.message.reply_text(tip_card(tip), parse_mode="Markdown")
async def summary_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
text = await get_daily_summary()
await update.message.reply_text(text, parse_mode="Markdown")
# ── Callback (inline button) handler ─────────────────────────────────────────
async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
query = update.callback_query
await query.answer()
if query.data == "tips":
tips = await get_tips()
if not tips:
await query.edit_message_text(" return
No strong value bets found right now.")
await query.edit_message_text("Here are today's value bets for tip in tips:
")
await query.message.reply_text(tip_card(tip), parse_mode="Markdown")
elif query.data == "summary":
text = await get_daily_summary()
await query.edit_message_text(text, parse_mode="Markdown")
elif query.data == "howto":
await query.edit_message_text(
" *How Value Betting Works*\n\n"
"Bookmakers set odds that include a margin (their profit). "
"A *value bet* exists when we calculate the true probability of an outcome "
"is higher than what the odds imply.\n\n"
"*Example:*\n"
"• Bookie odds: 2.50 → implied probability = 40%\n"
"• Our model: true probability = 50%\n"
"• Value Edge = +10% \n\n"
"Consistently backing +EV (positive expected value) bets "
"produces profit over the long run.\n\n"
" _Short-term variance is normal — always manage your bankroll._",
parse_mode="Markdown",
)
# ── Scheduled broadcast ───────────────────────────────────────────────────────
async def broadcast_tips(ctx: ContextTypes.DEFAULT_TYPE) -> None:
"""Automatically push tips to the channel."""
tips = await get_tips()
if not tips:
logger.info("Scheduled broadcast: no tips to send.")
return
header = (
f" *Daily Value Tips — {datetime.now().strftime('%d %b %Y')}*\n"
f"Found *{len(tips)}* value bet(s) today \n"
)
await ctx.bot.send_message(CHANNEL_ID, header, parse_mode="Markdown")
for tip in tips:
await ctx.bot.send_message(CHANNEL_ID, tip_card(tip), parse_mode="Markdown")
logger.info("Broadcast sent: %d tips.", len(tips))
# ── Admin: manual push ────────────────────────────────────────────────────────
async def push(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
if update.effective_user.id != ADMIN_ID:
await update.message.reply_text(" Admin only.")
return
await broadcast_tips(ctx)
await update.message.reply_text(" Tips pushed to channel.")
# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
app = Application.builder().token(BOT_TOKEN).build()
# Commands
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("tips", tips_command))
app.add_handler(CommandHandler("summary", summary_command))
app.add_handler(CommandHandler("push", push))
# Inline buttons
app.add_handler(CallbackQueryHandler(button))
# Scheduled job — broadcasts every day at 08:00 UTC
job_queue: JobQueue = app.job_queue
job_queue.run_daily(
broadcast_tips,
time=datetime.strptime("08:00", "%H:%M").time(),
)
logger.info("Bot is running…")
app.run_polling(drop_pending_updates=True)
if __name__ == "__main__":
main()
