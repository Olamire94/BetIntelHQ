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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0").strip())
if not BOT_TOKEN:
if not CHANNEL_ID:
raise RuntimeError("BOT_TOKEN environment variable is not set.")
raise RuntimeError("CHANNEL_ID environment variable is not set.")
def tip_card(tip: dict) -> str:
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
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
kb = [
[InlineKeyboardButton(" Today's Tips", callback_data="tips")],
[InlineKeyboardButton(" Daily Summary", callback_data="summary")],
[InlineKeyboardButton(" How It Works", callback_data="howto")],
]
await update.message.reply_text(
" *Value Bet Bot*\n\nI scan bookmakers for bets where the _true probability_ parse_mode="Markdown",
reply_markup=InlineKeyboardMarkup(kb),
beats
)
async def tips_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
msg = await update.message.reply_text(" Fetching value tips…")
try:
tips = await get_tips()
except Exception as e:
logger.error("get_tips failed: %s", e)
await msg.edit_text(" return
Error fetching tips. Check your ODDS_API_KEY.")
if not tips:
await msg.edit_text(" return
No strong value bets found right now.")
await msg.delete()
for tip in tips:
await update.message.reply_text(tip_card(tip), parse_mode="Markdown")
async def summary_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
try:
text = await get_daily_summary()
except Exception as e:
logger.error("summary failed: %s", e)
text = " Error fetching summary. Check your ODDS_API_KEY."
await update.message.reply_text(text, parse_mode="Markdown")
async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
query = update.callback_query
await query.answer()
if query.data == "tips":
try:
tips = await get_tips()
except Exception:
await query.edit_message_text(" return
Error fetching tips. Check your ODDS_API_KEY.")
if not tips:
return
await query.edit_message_text(" No strong value bets right now.")
await query.edit_message_text("Here are today's value bets ")
for tip in tips:
await query.message.reply_text(tip_card(tip), parse_mode="Markdown")
elif query.data == "summary":
text = await get_daily_summary()
await query.edit_message_text(text, parse_mode="Markdown")
elif query.data == "howto":
await query.edit_message_text(
" *How Value Betting Works*\n\nBookmakers set odds including their profit margi
"A *value bet* exists when our model finds the true probability is higher than th
"*Example:*\n• Bookie odds: 2.50 → implied 40%\n• Our model: true prob 50%\n• Edg
" _Always manage your bankroll responsibly._",
parse_mode="Markdown",
)
async def broadcast_tips(ctx: ContextTypes.DEFAULT_TYPE) -> None:
try:
tips = await get_tips()
except Exception as e:
logger.error("Broadcast failed: %s", e)
return
if not tips:
logger.info("Broadcast: no tips today.")
return
header = f" try:
*Daily Value Tips — {datetime.now().strftime('%d %b %Y')}*\nFound *{len(tip
await ctx.bot.send_message(CHANNEL_ID, header, parse_mode="Markdown")
for tip in tips:
await ctx.bot.send_message(CHANNEL_ID, tip_card(tip), parse_mode="Markdown")
logger.info("Broadcast: %d tips sent.", len(tips))
except Exception as e:
logger.error("Channel send failed: %s", e)
async def push(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
if update.effective_user.id != ADMIN_ID:
await update.message.reply_text(" Admin only.")
return
await update.message.reply_text(" Pushing tips…")
await broadcast_tips(ctx)
await update.message.reply_text(" Done.")
def main() -> None:
app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("tips", tips_command))
app.add_handler(CommandHandler("summary", summary_command))
app.add_handler(CommandHandler("push", push))
app.add_handler(CallbackQueryHandler(button))
app.job_queue.run_daily(broadcast_tips, time=dtime(hour=8, minute=0))
logger.info(" Bot running.")
app.run_polling(drop_pending_updates=True)
if __name__ == "__main__":
main()
