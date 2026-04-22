import os
import datetime
from pymongo import MongoClient
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.constants import ParseMode

# ─────────────────────────────────────────────
#  CONFIG — reads from Railway Environment Variables
# ─────────────────────────────────────────────
BOT_TOKEN    = os.environ.get("BOT_TOKEN")
MONGO_URI    = os.environ.get("MONGO_URI")
CHANNEL_ID   = os.environ.get("CHANNEL_ID")       # e.g. @mychannel
BOT_USERNAME = os.environ.get("BOT_USERNAME")      # without @
ADMIN_IDS    = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]

CHANNEL_LINK = f"https://t.me/{CHANNEL_ID.lstrip('@')}" if CHANNEL_ID else ""
DB_NAME      = "referral_bot"

REFERRAL_REWARD_INR = 5.00
USDT_TO_INR         = 83.50
MIN_WITHDRAW_INR    = 200.00

# ─────────────────────────────────────────────
#  MONGODB SETUP
# ─────────────────────────────────────────────
client     = MongoClient(MONGO_URI)
db         = client[DB_NAME]
users_col  = db["users"]
refs_col   = db["referrals"]
wdraw_col  = db["withdrawals"]

# Create indexes for fast lookup
users_col.create_index("user_id", unique=True)
refs_col.create_index("referrer_id")
wdraw_col.create_index("user_id")

# ─────────────────────────────────────────────
#  DATABASE HELPERS
# ─────────────────────────────────────────────
def get_user(user_id):
    return users_col.find_one({"user_id": user_id})

def create_user(user_id, username, full_name, referred_by=None):
    if not get_user(user_id):
        users_col.insert_one({
            "user_id":     user_id,
            "username":    username,
            "full_name":   full_name,
            "referred_by": referred_by,
            "balance_inr": 0.0,
            "total_refs":  0,
            "joined_at":   datetime.datetime.utcnow(),
            "is_banned":   False
        })

def add_balance(user_id, amount_inr):
    users_col.update_one(
        {"user_id": user_id},
        {"$inc": {"balance_inr": amount_inr}}
    )

def increment_refs(user_id):
    users_col.update_one(
        {"user_id": user_id},
        {"$inc": {"total_refs": 1}}
    )

def inr_to_usdt(inr):
    return round(inr / USDT_TO_INR, 4)

def get_referral_link(user_id):
    return f"https://t.me/{BOT_USERNAME}?start=ref{user_id}"

# ─────────────────────────────────────────────
#  CHANNEL CHECK
# ─────────────────────────────────────────────
async def is_member(bot, user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False

# ─────────────────────────────────────────────
#  KEYBOARDS
# ─────────────────────────────────────────────
def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 My Balance",   callback_data="balance"),
         InlineKeyboardButton("👥 Referrals",    callback_data="referrals")],
        [InlineKeyboardButton("💸 Withdraw",     callback_data="withdraw"),
         InlineKeyboardButton("🔗 My Ref Link",  callback_data="reflink")],
        [InlineKeyboardButton("📊 Stats",        callback_data="stats"),
         InlineKeyboardButton("ℹ️ How It Works", callback_data="howto")],
        [InlineKeyboardButton("📋 My History",   callback_data="history"),
         InlineKeyboardButton("🆘 Support",      callback_data="support")],
        [InlineKeyboardButton("❤️ Support Us / Donate", callback_data="donate")],
    ])

def back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("« Back to Menu", callback_data="menu")]])

def withdraw_method_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏦 UPI",         callback_data="w_upi")],
        [InlineKeyboardButton("🅿️ PayPal",      callback_data="w_paypal")],
        [InlineKeyboardButton("💎 USDT BEP-20", callback_data="w_usdt")],
        [InlineKeyboardButton("« Cancel",       callback_data="menu")],
    ])

# ─────────────────────────────────────────────
#  START / WELCOME
# ─────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = ctx.args

    if not await is_member(ctx.bot, user.id):
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK),
            InlineKeyboardButton("✅ I Joined", callback_data=f"check_join_{'_'.join(args) if args else ''}"),
        ]])
        await update.message.reply_text(
            "╔══════════════════════════╗\n"
            "║  🔐  *Access Required*    ║\n"
            "╚══════════════════════════╝\n\n"
            "To use this bot you must join our official channel first.\n\n"
            "👇 Tap below, join, then press *I Joined*.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb
        )
        return

    await _register_and_welcome(update, ctx, user, args)

async def _register_and_welcome(update, ctx, user, args):
    existing = get_user(user.id)
    referred_by = None

    if not existing:
        if args and args[0].startswith("ref"):
            try:
                ref_id = int(args[0][3:])
                if ref_id != user.id and get_user(ref_id):
                    referred_by = ref_id
            except ValueError:
                pass

        create_user(user.id, user.username, user.full_name, referred_by)

        if referred_by:
            add_balance(referred_by, REFERRAL_REWARD_INR)
            increment_refs(referred_by)
            refs_col.insert_one({
                "referrer_id": referred_by,
                "referee_id":  user.id,
                "reward_inr":  REFERRAL_REWARD_INR,
                "earned_at":   datetime.datetime.utcnow()
            })
            try:
                await ctx.bot.send_message(
                    referred_by,
                    f"🎉 *New Referral!*\n\n"
                    f"*{user.full_name}* joined using your link!\n"
                    f"💰 *+₹{REFERRAL_REWARD_INR:.2f}* added to your balance.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass

    db_user = get_user(user.id)
    usdt_bal = inr_to_usdt(db_user["balance_inr"])

    welcome = (
        f"╔══════════════════════════════╗\n"
        f"║   💎  *REFERRAL REWARDS BOT*   ║\n"
        f"╚══════════════════════════════╝\n\n"
        f"Welcome back, *{user.first_name}*! 👋\n\n"
        f"┌─────────────────────────┐\n"
        f"│  💵 Balance: *₹{db_user['balance_inr']:.2f}*\n"
        f"│  🔶 USDT:    *${usdt_bal}*\n"
        f"│  👥 Referrals: *{db_user['total_refs']}*\n"
        f"└─────────────────────────┘\n\n"
        f"📌 Earn *₹{REFERRAL_REWARD_INR:.0f}* for every friend you refer!\n"
        f"💸 Min withdrawal: *₹{MIN_WITHDRAW_INR:.0f}*\n\n"
        f"Choose an option below 👇"
    )

    if update.message:
        await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())
    else:
        await update.callback_query.edit_message_text(welcome, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())

# ─────────────────────────────────────────────
#  CHECK JOIN CALLBACK
# ─────────────────────────────────────────────
async def check_join_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not await is_member(ctx.bot, user.id):
        await query.answer("❌ You haven't joined yet! Please join the channel first.", show_alert=True)
        return

    suffix = query.data[len("check_join_"):]
    args = [suffix] if suffix else []
    await _register_and_welcome(update, ctx, user, args)

# ─────────────────────────────────────────────
#  BUTTON HANDLER
# ─────────────────────────────────────────────
async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data    = query.data
    user_id = update.effective_user.id
    db_user = get_user(user_id)

    if not db_user:
        await query.edit_message_text("Please /start the bot first.")
        return

    # ── MENU ──
    if data == "menu":
        usdt_bal = inr_to_usdt(db_user["balance_inr"])
        text = (
            f"╔══════════════════════════════╗\n"
            f"║   💎  *REFERRAL REWARDS BOT*   ║\n"
            f"╚══════════════════════════════╝\n\n"
            f"Welcome, *{update.effective_user.first_name}*! 👋\n\n"
            f"┌─────────────────────────┐\n"
            f"│  💵 Balance: *₹{db_user['balance_inr']:.2f}*\n"
            f"│  🔶 USDT:    *${usdt_bal}*\n"
            f"│  👥 Referrals: *{db_user['total_refs']}*\n"
            f"└─────────────────────────┘\n\n"
            f"Choose an option below 👇"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())

    # ── BALANCE ──
    elif data == "balance":
        inr  = db_user["balance_inr"]
        usdt = inr_to_usdt(inr)
        text = (
            f"💰 *Your Balance*\n\n"
            f"┌──────────────────────────┐\n"
            f"│  🇮🇳 INR:  *₹{inr:.2f}*\n"
            f"│  🔶 USDT: *${usdt}*\n"
            f"│  👥 Refs:  *{db_user['total_refs']}*\n"
            f"└──────────────────────────┘\n\n"
            f"💡 Rate: $1 USDT = ₹{USDT_TO_INR}\n"
            f"📌 Min Withdrawal: *₹{MIN_WITHDRAW_INR:.0f}*\n"
            f"{'✅ Ready to withdraw!' if inr >= MIN_WITHDRAW_INR else f'⚠️ Need ₹{MIN_WITHDRAW_INR - inr:.2f} more to withdraw'}"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

    # ── REFERRALS ──
    elif data == "referrals":
        refs = list(refs_col.find({"referrer_id": user_id}).sort("earned_at", -1).limit(10))
        link = get_referral_link(user_id)
        ref_list = ""
        for r in refs:
            u = get_user(r["referee_id"])
            name = u["full_name"] if u else "Unknown"
            dt   = r["earned_at"].strftime("%Y-%m-%d") if isinstance(r["earned_at"], datetime.datetime) else str(r["earned_at"])[:10]
            ref_list += f"  • {name} — *+₹{r['reward_inr']:.2f}* ({dt})\n"

        text = (
            f"👥 *Your Referrals*\n\n"
            f"Total: *{db_user['total_refs']}* referrals\n"
            f"Earned: *₹{db_user['balance_inr']:.2f}*\n\n"
            f"🔗 Your link:\n`{link}`\n\n"
            + (f"📋 *Recent Referrals:*\n{ref_list}" if refs else "No referrals yet. Share your link!")
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

    # ── REF LINK ──
    elif data == "reflink":
        link = get_referral_link(user_id)
        share_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={link}&text=Join+and+earn+rewards!")],
            [InlineKeyboardButton("« Back to Menu", callback_data="menu")]
        ])
        text = (
            f"🔗 *Your Referral Link*\n\n"
            f"`{link}`\n\n"
            f"📤 Share this link with friends!\n"
            f"💰 Earn *₹{REFERRAL_REWARD_INR:.0f}* for every friend who joins.\n\n"
            f"📊 You've referred *{db_user['total_refs']}* people so far!"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=share_kb)

    # ── STATS ──
    elif data == "stats":
        total_users = users_col.count_documents({})
        total_refs  = refs_col.count_documents({})
        pipeline    = [{"$match": {"status": "approved"}}, {"$group": {"_id": None, "total": {"$sum": "$amount_inr"}}}]
        paid_result = list(wdraw_col.aggregate(pipeline))
        total_paid  = paid_result[0]["total"] if paid_result else 0
        text = (
            f"📊 *Bot Statistics*\n\n"
            f"┌─────────────────────────┐\n"
            f"│  👤 Total Users:  *{total_users}*\n"
            f"│  🔗 Total Refs:   *{total_refs}*\n"
            f"│  💸 Total Paid:   *₹{total_paid:.2f}*\n"
            f"│  💱 Rate:         *₹{USDT_TO_INR}/USDT*\n"
            f"└─────────────────────────┘"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

    # ── HOW IT WORKS ──
    elif data == "howto":
        text = (
            f"ℹ️ *How It Works*\n\n"
            f"1️⃣ *Get Your Link* — Tap 🔗 My Ref Link\n"
            f"2️⃣ *Share It* — Send to friends, post on social media\n"
            f"3️⃣ *Earn* — Get *₹{REFERRAL_REWARD_INR:.0f}* every time someone joins\n"
            f"4️⃣ *Withdraw* — Once you reach *₹{MIN_WITHDRAW_INR:.0f}*, withdraw via:\n"
            f"   • 🏦 UPI\n   • 🅿️ PayPal\n   • 💎 USDT BEP-20\n\n"
            f"💡 *Conversion:* ₹{USDT_TO_INR} = 1 USDT\n"
            f"⚡ Withdrawals processed within 24–48 hours."
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

    # ── HISTORY ──
    elif data == "history":
        rows = list(wdraw_col.find({"user_id": user_id}).sort("requested_at", -1).limit(10))
        if not rows:
            text = "📋 *Withdrawal History*\n\nNo withdrawals yet."
        else:
            lines = ""
            for r in rows:
                emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌"}.get(r["status"], "•")
                dt = r["requested_at"].strftime("%Y-%m-%d") if isinstance(r["requested_at"], datetime.datetime) else str(r["requested_at"])[:10]
                lines += f"{emoji} *{r['method'].upper()}* — ₹{r['amount_inr']:.2f} ({r['status']}) — {dt}\n"
            text = f"📋 *Withdrawal History*\n\n{lines}"
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

    # ── DONATE ──
    elif data == "donate":
        # UPI ID is obfuscated — split across variables so it's not plainly visible in code
        _a = "9410988578"
        _b = "@fam"
        upi_id = _a + _b
        # UPI deep link — opens any UPI app directly
        upi_url = f"upi://pay?pa={upi_id}&pn=SupportBot&cu=INR"
        donate_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Pay via UPI App", url=upi_url)],
            [InlineKeyboardButton("« Back to Menu", callback_data="menu")]
        ])
        text = (
            "❤️ *Support Us*\n\n"
            "This bot is free to use and kept alive by your generous support.\n\n"
            "Every donation — big or small — helps us keep paying for servers "
            "and adding new features for you! 🙏\n\n"
            "┌──────────────────────────┐\n"
            "│  💸 Any amount welcome   │\n"
            "│  🔒 100% secure via UPI  │\n"
            "└──────────────────────────┘\n\n"
            "👇 Tap the button below to open your UPI app:"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=donate_kb)


        text = (
            f"🆘 *Support*\n\n"
            f"Need help? Contact our admin:\n"
            f"📩 @AdminUsername\n\n"
            f"Common issues:\n"
            f"• Withdrawal not received → wait 24–48h\n"
            f"• Referral not credited → ensure friend joined via your link\n"
            f"• Wrong payment address → contact admin immediately"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

    # ── WITHDRAW ──
    elif data == "withdraw":
        inr = db_user["balance_inr"]
        if inr < MIN_WITHDRAW_INR:
            text = (
                f"💸 *Withdraw*\n\n"
                f"❌ Insufficient balance!\n\n"
                f"Your balance: *₹{inr:.2f}*\n"
                f"Minimum required: *₹{MIN_WITHDRAW_INR:.0f}*\n"
                f"Need *₹{MIN_WITHDRAW_INR - inr:.2f}* more.\n\n"
                f"Keep referring to earn more! 🔗"
            )
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())
        else:
            usdt = inr_to_usdt(inr)
            text = (
                f"💸 *Withdraw Funds*\n\n"
                f"Available: *₹{inr:.2f}* (${usdt} USDT)\n\n"
                f"Choose your withdrawal method:"
            )
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=withdraw_method_kb())

    # ── WITHDRAW METHOD ──
    elif data in ("w_upi", "w_paypal", "w_usdt"):
        method_map = {"w_upi": "UPI", "w_paypal": "PayPal", "w_usdt": "USDT BEP-20"}
        addr_map   = {"w_upi": "your UPI ID (e.g. name@upi)", "w_paypal": "your PayPal email", "w_usdt": "your BEP-20 wallet address"}
        method = method_map[data]
        ctx.user_data["withdraw_method"] = method
        ctx.user_data["awaiting_withdraw_addr"] = True
        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data="menu")]])
        text = (
            f"💸 *Withdraw via {method}*\n\n"
            f"Please send me {addr_map[data]}.\n\n"
            f"⚠️ Double-check your address before sending!"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=cancel_kb)

# ─────────────────────────────────────────────
#  WITHDRAW ADDRESS INPUT
# ─────────────────────────────────────────────
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db_user = get_user(user_id)
    if not db_user:
        return

    if ctx.user_data.get("awaiting_withdraw_addr"):
        address = update.message.text.strip()
        method  = ctx.user_data.get("withdraw_method", "Unknown")
        inr     = db_user["balance_inr"]
        usdt    = inr_to_usdt(inr)

        wdraw_col.insert_one({
            "user_id":      user_id,
            "amount_inr":   inr,
            "amount_usdt":  usdt,
            "method":       method,
            "address":      address,
            "status":       "pending",
            "requested_at": datetime.datetime.utcnow(),
            "processed_at": None
        })
        users_col.update_one({"user_id": user_id}, {"$set": {"balance_inr": 0.0}})
        ctx.user_data["awaiting_withdraw_addr"] = False

        text = (
            f"✅ *Withdrawal Request Submitted!*\n\n"
            f"┌──────────────────────────┐\n"
            f"│  Method: *{method}*\n"
            f"│  Amount: *₹{inr:.2f}* (${usdt} USDT)\n"
            f"│  Address: `{address}`\n"
            f"│  Status: *Pending* ⏳\n"
            f"└──────────────────────────┘\n\n"
            f"⏱ Processing time: 24–48 hours\n"
            f"📩 You'll be notified once processed."
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())

        for admin_id in ADMIN_IDS:
            try:
                await ctx.bot.send_message(
                    admin_id,
                    f"🔔 *New Withdrawal Request*\n\n"
                    f"User: [{update.effective_user.full_name}](tg://user?id={user_id}) (`{user_id}`)\n"
                    f"Method: *{method}*\n"
                    f"Amount: *₹{inr:.2f}* (${usdt} USDT)\n"
                    f"Address: `{address}`\n\n"
                    f"/approve {user_id} | /reject {user_id}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass

# ─────────────────────────────────────────────
#  ADMIN COMMANDS
# ─────────────────────────────────────────────
async def admin_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        uid = int(ctx.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /approve <user_id>")
        return
    wdraw_col.update_one(
        {"user_id": uid, "status": "pending"},
        {"$set": {"status": "approved", "processed_at": datetime.datetime.utcnow()}}
    )
    await update.message.reply_text(f"✅ Withdrawal approved for user {uid}")
    try:
        await ctx.bot.send_message(uid, "✅ *Your withdrawal has been approved!*\nFunds will arrive shortly.", parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

async def admin_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        uid = int(ctx.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /reject <user_id>")
        return
    row = wdraw_col.find_one({"user_id": uid, "status": "pending"})
    if row:
        users_col.update_one({"user_id": uid}, {"$inc": {"balance_inr": row["amount_inr"]}})
    wdraw_col.update_one(
        {"user_id": uid, "status": "pending"},
        {"$set": {"status": "rejected", "processed_at": datetime.datetime.utcnow()}}
    )
    await update.message.reply_text(f"❌ Withdrawal rejected for user {uid}, balance refunded.")
    try:
        await ctx.bot.send_message(uid, "❌ *Your withdrawal was rejected.*\nBalance has been refunded. Contact support.", parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

async def admin_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    total_users  = users_col.count_documents({})
    total_refs   = refs_col.count_documents({})
    pending      = list(wdraw_col.aggregate([{"$match": {"status": "pending"}},  {"$group": {"_id": None, "count": {"$sum": 1}, "total": {"$sum": "$amount_inr"}}}]))
    approved     = list(wdraw_col.aggregate([{"$match": {"status": "approved"}}, {"$group": {"_id": None, "count": {"$sum": 1}, "total": {"$sum": "$amount_inr"}}}]))
    p = pending[0]  if pending  else {"count": 0, "total": 0}
    a = approved[0] if approved else {"count": 0, "total": 0}
    text = (
        f"🛡 *Admin Stats*\n\n"
        f"👤 Total Users: *{total_users}*\n"
        f"🔗 Total Referrals: *{total_refs}*\n"
        f"⏳ Pending Withdrawals: *{p['count']}* (₹{p['total']:.2f})\n"
        f"✅ Paid Out: *{a['count']}* (₹{a['total']:.2f})"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def admin_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        uid = int(ctx.args[0])
        users_col.update_one({"user_id": uid}, {"$set": {"is_banned": True}})
        await update.message.reply_text(f"🚫 User {uid} banned.")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# ─────────────────────────────────────────────
#  BROADCAST FEATURES
# ─────────────────────────────────────────────

# Helper to get all active users
def get_all_users():
    return list(users_col.find({"is_banned": False}, {"user_id": 1}))

# /broadcast <message>
# Sends a text message to all users
async def broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not ctx.args:
        await update.message.reply_text(
            "📢 *Broadcast Text*\n\nUsage:\n`/broadcast Your message here`\n\n"
            "Example:\n`/broadcast 🎉 New update is live!`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    msg = " ".join(ctx.args)
    all_users = get_all_users()
    progress = await update.message.reply_text(f"📤 Sending to {len(all_users)} users...")
    sent = failed = 0
    for u in all_users:
        try:
            await ctx.bot.send_message(
                u["user_id"],
                f"📢 *Announcement*\n\n{msg}",
                parse_mode=ParseMode.MARKDOWN
            )
            sent += 1
        except Exception:
            failed += 1
    await progress.edit_text(
        f"✅ *Broadcast Complete!*\n\n"
        f"📤 Sent: *{sent}*\n"
        f"❌ Failed: *{failed}* (blocked/inactive users)",
        parse_mode=ParseMode.MARKDOWN
    )

# /broadcast_button <button text> | <button url> | <message>
# Sends a message with a clickable button to all users
async def broadcast_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not ctx.args:
        await update.message.reply_text(
            "🔘 *Broadcast with Button*\n\nUsage:\n"
            "`/broadcast_button Button Text | https://link.com | Your message here`\n\n"
            "Example:\n"
            "`/broadcast_button Join Now | https://t.me/mychannel | 🎉 Join our channel for rewards!`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    full = " ".join(ctx.args)
    parts = [p.strip() for p in full.split("|")]
    if len(parts) < 3:
        await update.message.reply_text(
            "❌ Wrong format!\n\nUse:\n`/broadcast_button Button Text | https://link.com | Your message`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    btn_text, btn_url, msg = parts[0], parts[1], parts[2]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(btn_text, url=btn_url)]])
    all_users = get_all_users()
    progress = await update.message.reply_text(f"📤 Sending to {len(all_users)} users...")
    sent = failed = 0
    for u in all_users:
        try:
            await ctx.bot.send_message(
                u["user_id"],
                f"📢 *Announcement*\n\n{msg}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb
            )
            sent += 1
        except Exception:
            failed += 1
    await progress.edit_text(
        f"✅ *Broadcast with Button Complete!*\n\n"
        f"📤 Sent: *{sent}*\n"
        f"❌ Failed: *{failed}*",
        parse_mode=ParseMode.MARKDOWN
    )

# /broadcast_photo <caption>  (send with a photo attached)
# Admin sends this command as a REPLY to a photo
async def broadcast_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    # Must be used as reply to a photo message
    if not update.message.reply_to_message or not update.message.reply_to_message.photo:
        await update.message.reply_text(
            "🖼 *Broadcast with Photo*\n\n"
            "How to use:\n"
            "1️⃣ Send a photo to the bot\n"
            "2️⃣ *Reply* to that photo with:\n"
            "`/broadcast_photo Your caption here`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    photo_id = update.message.reply_to_message.photo[-1].file_id
    caption  = " ".join(ctx.args) if ctx.args else "📢 New Announcement!"
    all_users = get_all_users()
    progress = await update.message.reply_text(f"📤 Sending photo to {len(all_users)} users...")
    sent = failed = 0
    for u in all_users:
        try:
            await ctx.bot.send_photo(
                u["user_id"],
                photo=photo_id,
                caption=f"📢 *Announcement*\n\n{caption}",
                parse_mode=ParseMode.MARKDOWN
            )
            sent += 1
        except Exception:
            failed += 1
    await progress.edit_text(
        f"✅ *Photo Broadcast Complete!*\n\n"
        f"📤 Sent: *{sent}*\n"
        f"❌ Failed: *{failed}*",
        parse_mode=ParseMode.MARKDOWN
    )

# /msg <user_id> <message>
# Send a private message to one specific user
async def msg_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text(
            "💬 *Message One User*\n\nUsage:\n`/msg 987654321 Your message here`\n\n"
            "Example:\n`/msg 987654321 Your withdrawal has been processed!`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    try:
        uid = int(ctx.args[0])
        msg = " ".join(ctx.args[1:])
        await ctx.bot.send_message(
            uid,
            f"💬 *Message from Admin*\n\n{msg}",
            parse_mode=ParseMode.MARKDOWN
        )
        await update.message.reply_text(f"✅ Message sent to user `{uid}`", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID! Must be a number.")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to send: {e}")

# /admin_help — shows all admin commands
async def admin_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    text = (
        "🛡 *Admin Commands*\n\n"
        "━━━━ 👥 User Management ━━━━\n"
        "`/users` — List all users (ID + name)\n"
        "`/users 2` — Page 2 of user list\n"
        "`/userinfo <id>` — Full details of one user\n"
        "`/allbalances` — All users sorted by balance\n"
        "`/addbalance <id> <₹>` — Add balance to user\n"
        "`/ban <id>` — Ban a user\n\n"
        "━━━━ 💸 Withdrawals ━━━━\n"
        "`/pendings` — All pending withdrawals\n"
        "`/approve <id>` — Approve withdrawal\n"
        "`/reject <id>` — Reject & refund\n\n"
        "━━━━ 📢 Broadcast ━━━━\n"
        "`/broadcast <msg>` — Text to all users\n"
        "`/broadcast_button Btn|URL|msg` — With button\n"
        "`/broadcast_photo <caption>` — Reply to photo\n"
        "`/msg <id> <msg>` — Message one user\n\n"
        "━━━━ 📊 Stats ━━━━\n"
        "`/astats` — Bot statistics\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────
#  ADMIN USER MANAGEMENT COMMANDS
# ─────────────────────────────────────────────

# /users — list all users with name and ID (paginated, 20 per page)
async def list_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    page = 0
    if ctx.args:
        try:
            page = int(ctx.args[0]) - 1
        except ValueError:
            page = 0
    per_page = 20
    skip     = page * per_page
    total    = users_col.count_documents({})
    all_u    = list(users_col.find({}, {"user_id": 1, "full_name": 1, "username": 1, "balance_inr": 1, "total_refs": 1, "is_banned": 1})
                    .sort("joined_at", -1).skip(skip).limit(per_page))
    if not all_u:
        await update.message.reply_text("No users found.")
        return
    lines = ""
    for u in all_u:
        banned = " 🚫" if u.get("is_banned") else ""
        uname  = f"@{u['username']}" if u.get("username") else "no username"
        lines += f"`{u['user_id']}`{banned} — *{u['full_name']}* ({uname})\n"
    total_pages = (total + per_page - 1) // per_page
    text = (
        f"👥 *All Users* — Page {page+1}/{total_pages} (Total: {total})\n\n"
        f"{lines}\n"
        f"➡️ Next page: `/users {page+2}`" if page + 1 < total_pages else
        f"👥 *All Users* — Page {page+1}/{total_pages} (Total: {total})\n\n{lines}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# /userinfo <user_id> — full details of one user
async def user_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not ctx.args:
        await update.message.reply_text("Usage: `/userinfo <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    u = get_user(uid)
    if not u:
        await update.message.reply_text("❌ User not found.")
        return
    # Count referrals made by this user
    ref_count  = refs_col.count_documents({"referrer_id": uid})
    # Count withdrawals
    w_pending  = wdraw_col.count_documents({"user_id": uid, "status": "pending"})
    w_approved = wdraw_col.count_documents({"user_id": uid, "status": "approved"})
    w_total    = wdraw_col.aggregate([{"$match": {"user_id": uid, "status": "approved"}}, {"$group": {"_id": None, "s": {"$sum": "$amount_inr"}}}])
    w_total    = list(w_total)
    paid_out   = w_total[0]["s"] if w_total else 0.0
    joined     = u["joined_at"].strftime("%Y-%m-%d %H:%M") if isinstance(u["joined_at"], datetime.datetime) else str(u["joined_at"])[:16]
    usdt       = inr_to_usdt(u["balance_inr"])
    referred_by_name = "None"
    if u.get("referred_by"):
        ref_user = get_user(u["referred_by"])
        referred_by_name = ref_user["full_name"] if ref_user else str(u["referred_by"])
    text = (
        f"👤 *User Details*\n\n"
        f"┌────────────────────────────┐\n"
        f"│ Name:   *{u['full_name']}*\n"
        f"│ ID:     `{uid}`\n"
        f"│ @:      @{u.get('username') or 'none'}\n"
        f"│ Joined: {joined}\n"
        f"│ Banned: {'🚫 Yes' if u.get('is_banned') else '✅ No'}\n"
        f"├────────────────────────────┤\n"
        f"│ 💵 Balance INR:  ₹{u['balance_inr']:.2f}\n"
        f"│ 🔶 Balance USDT: ${usdt}\n"
        f"│ 👥 Referrals:    {ref_count}\n"
        f"├────────────────────────────┤\n"
        f"│ 💸 Pending W/D:  {w_pending}\n"
        f"│ ✅ Approved W/D: {w_approved}\n"
        f"│ 💰 Total Paid:   ₹{paid_out:.2f}\n"
        f"│ 🔗 Referred by:  {referred_by_name}\n"
        f"└────────────────────────────┘\n\n"
        f"Actions:\n"
        f"`/ban {uid}` — Ban user\n"
        f"`/msg {uid} <text>` — Message user\n"
        f"`/addbalance {uid} <amount>` — Add balance"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# /allbalances — show all users sorted by highest balance
async def all_balances(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    top = list(users_col.find({}, {"user_id": 1, "full_name": 1, "balance_inr": 1, "total_refs": 1})
               .sort("balance_inr", -1).limit(25))
    if not top:
        await update.message.reply_text("No users yet.")
        return
    lines = ""
    for i, u in enumerate(top, 1):
        lines += f"{i}. `{u['user_id']}` — *{u['full_name']}* — ₹{u['balance_inr']:.2f} ({u['total_refs']} refs)\n"
    await update.message.reply_text(
        f"💰 *All User Balances* (Top 25)\n\n{lines}",
        parse_mode=ParseMode.MARKDOWN
    )

# /addbalance <user_id> <amount> — manually add balance to a user
async def add_balance_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text("Usage: `/addbalance <user_id> <amount>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid    = int(ctx.args[0])
        amount = float(ctx.args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID or amount.")
        return
    u = get_user(uid)
    if not u:
        await update.message.reply_text("❌ User not found.")
        return
    add_balance(uid, amount)
    await update.message.reply_text(
        f"✅ Added *₹{amount:.2f}* to `{uid}` ({u['full_name']})\n"
        f"New balance: *₹{u['balance_inr'] + amount:.2f}*",
        parse_mode=ParseMode.MARKDOWN
    )
    try:
        await ctx.bot.send_message(
            uid,
            f"🎁 *Bonus Added!*\n\n*₹{amount:.2f}* has been added to your balance by admin!\n"
            f"New balance: *₹{u['balance_inr'] + amount:.2f}*",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

# /pendings — show all pending withdrawal requests
async def pending_withdrawals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    rows = list(wdraw_col.find({"status": "pending"}).sort("requested_at", 1).limit(20))
    if not rows:
        await update.message.reply_text("✅ No pending withdrawals!")
        return
    lines = ""
    for r in rows:
        u    = get_user(r["user_id"])
        name = u["full_name"] if u else "Unknown"
        dt   = r["requested_at"].strftime("%m/%d %H:%M") if isinstance(r["requested_at"], datetime.datetime) else str(r["requested_at"])[:13]
        lines += (
            f"👤 *{name}* (`{r['user_id']}`)\n"
            f"   💸 ₹{r['amount_inr']:.2f} via *{r['method']}*\n"
            f"   📬 `{r['address']}`\n"
            f"   🕐 {dt}\n"
            f"   `/approve {r['user_id']}` | `/reject {r['user_id']}`\n\n"
        )
    await update.message.reply_text(
        f"⏳ *Pending Withdrawals* ({len(rows)})\n\n{lines}",
        parse_mode=ParseMode.MARKDOWN
    )

# ─────────────────────────────────────────────
#  EXTRA USER COMMANDS
# ─────────────────────────────────────────────
async def leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    top = list(users_col.find({}, {"full_name": 1, "total_refs": 1, "balance_inr": 1}).sort("total_refs", -1).limit(10))
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines  = ""
    for i, u in enumerate(top):
        lines += f"{medals[i]} *{u['full_name']}* — {u['total_refs']} refs — ₹{u['balance_inr']:.2f}\n"
    await update.message.reply_text(f"🏆 *Top Referrers*\n\n{lines or 'No data yet.'}", parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

async def profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db_user = get_user(user_id)
    if not db_user:
        await update.message.reply_text("Please /start first.")
        return
    link = get_referral_link(user_id)
    usdt = inr_to_usdt(db_user["balance_inr"])
    joined = db_user["joined_at"].strftime("%Y-%m-%d") if isinstance(db_user["joined_at"], datetime.datetime) else str(db_user["joined_at"])[:10]
    text = (
        f"👤 *Your Profile*\n\n"
        f"Name: *{db_user['full_name']}*\n"
        f"ID: `{user_id}`\n"
        f"Joined: {joined}\n\n"
        f"💵 Balance INR: *₹{db_user['balance_inr']:.2f}*\n"
        f"🔶 Balance USDT: *${usdt}*\n"
        f"👥 Total Refs: *{db_user['total_refs']}*\n\n"
        f"🔗 Your Link:\n`{link}`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())

async def rate_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"💱 *Current Exchange Rate*\n\n"
        f"₹1 = ${inr_to_usdt(1)} USDT\n"
        f"$1 USDT = ₹{USDT_TO_INR}",
        parse_mode=ParseMode.MARKDOWN
    )

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("profile",     profile))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("rate",        rate_cmd))
    app.add_handler(CommandHandler("approve",     admin_approve))
    app.add_handler(CommandHandler("reject",      admin_reject))
    app.add_handler(CommandHandler("astats",      admin_stats))
    app.add_handler(CommandHandler("ban",         admin_ban))
    app.add_handler(CommandHandler("users",       list_users))
    app.add_handler(CommandHandler("userinfo",    user_info))
    app.add_handler(CommandHandler("allbalances", all_balances))
    app.add_handler(CommandHandler("addbalance",  add_balance_cmd))
    app.add_handler(CommandHandler("pendings",    pending_withdrawals))
    app.add_handler(CommandHandler("broadcast",        broadcast))
    app.add_handler(CommandHandler("broadcast_button", broadcast_button))
    app.add_handler(CommandHandler("broadcast_photo",  broadcast_photo))
    app.add_handler(CommandHandler("msg",              msg_user))
    app.add_handler(CommandHandler("admin_help",       admin_help))

    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join_"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Referral Bot (MongoDB) is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
