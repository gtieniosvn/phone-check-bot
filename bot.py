import os
import sys
import asyncio
import logging
import time
import json
import sqlite3
import re
from datetime import datetime

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ParseMode

# ===== CONFIG =====
BOT_TOKEN = os.getenv("BOT_TOKEN", "8615329683:AAGPZsQnYU4Lg-CgqSUSxAswbF5c2vB6EUU")
ADMIN_IDS = [8666979500]
ADMIN_USERNAME = "@trangiatien33"
BANK_INFO = {
    'bank': 'MB Bank',
    'account': '8666979500',
    'name': 'TRAN GIA TIEN'
}
API_KEYS = {
    'numverify': 'b78bdc3b13aeecbbe04937deed4169b9',
    'abstract': '3962ee6eb6fb48f5a1190e7e90599d89',
    'veriphone': '2DF882439DDC43C29E21BA9087071D57'
}
PACKAGES = [
    {'name': 'Gói Cơ Bản', 'amount': 10000, 'emoji': '🥉'},
    {'name': 'Gói Tiết Kiệm', 'amount': 20000, 'emoji': '🥈'},
    {'name': 'Gói Phổ Thông', 'amount': 50000, 'emoji': '🥇'},
    {'name': 'Gói Cao Cấp', 'amount': 100000, 'emoji': '💎'},
    {'name': 'Gói VIP', 'amount': 200000, 'emoji': '👑'},
    {'name': 'Gói Đại Gia', 'amount': 500000, 'emoji': '🌟'}
]
PRICE_PER_CHECK = 5000

# ===== LOGGING =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ===== DATABASE =====
class Database:
    def __init__(self):
        self.conn = sqlite3.connect("phone_bot.db", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._setup()

    def _row(self, row):
        if row is None:
            return None
        return {k: row[k] for k in row.keys()}

    def _setup(self):
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
            balance REAL DEFAULT 0, total_checks INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0,
            total_deposited REAL DEFAULT 0, total_spent REAL DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS deposit_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            amount REAL, package_name TEXT DEFAULT '', status TEXT DEFAULT 'pending',
            admin_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        for aid in ADMIN_IDS:
            c.execute('INSERT OR IGNORE INTO users (user_id, is_admin, balance) VALUES (?, 1, 999999)', (aid,))
        self.conn.commit()

    def get_user(self, uid):
        return self._row(self.conn.execute('SELECT * FROM users WHERE user_id=?', (uid,)).fetchone())

    def create_user(self, uid, username=None, full_name=None):
        bal = 999999 if uid in ADMIN_IDS else 0
        self.conn.execute('INSERT OR IGNORE INTO users (user_id, username, full_name, balance) VALUES (?,?,?,?)',
                         (uid, username, full_name, bal))
        self.conn.commit()

    def get_balance(self, uid):
        r = self.conn.execute('SELECT balance FROM users WHERE user_id=?', (uid,)).fetchone()
        return r[0] if r else 0

    def update_balance(self, uid, amount):
        cur = self.get_balance(uid)
        new = cur + amount
        if new < 0:
            return False
        c = self.conn.cursor()
        c.execute('UPDATE users SET balance=? WHERE user_id=?', (new, uid))
        if amount > 0:
            c.execute('UPDATE users SET total_deposited=total_deposited+? WHERE user_id=?', (amount, uid))
        else:
            c.execute('UPDATE users SET total_spent=total_spent+? WHERE user_id=?', (abs(amount), uid))
        self.conn.commit()
        return True

    def create_request(self, uid, amount, pkg=""):
        c = self.conn.cursor()
        c.execute('INSERT INTO deposit_requests (user_id, amount, package_name) VALUES (?,?,?)', (uid, amount, pkg))
        self.conn.commit()
        return c.lastrowid

    def get_pending(self):
        return [self._row(r) for r in self.conn.execute(
            'SELECT * FROM deposit_requests WHERE status="pending" ORDER BY created_at DESC').fetchall()]

    def approve(self, rid, aid):
        r = self.conn.execute('SELECT * FROM deposit_requests WHERE id=? AND status="pending"', (rid,)).fetchone()
        if not r:
            return False, None
        d = self._row(r)
        self.conn.execute('UPDATE deposit_requests SET status="approved", admin_id=? WHERE id=?', (aid, rid))
        self.update_balance(d['user_id'], d['amount'])
        self.conn.commit()
        return True, d

    def reject(self, rid, aid):
        self.conn.execute('UPDATE deposit_requests SET status="rejected", admin_id=? WHERE id=?', (aid, rid))
        self.conn.commit()

    def get_stats(self):
        c = self.conn.cursor()
        return {
            'users': c.execute('SELECT COUNT(*) FROM users').fetchone()[0],
            'pending': c.execute('SELECT COUNT(*) FROM deposit_requests WHERE status="pending"').fetchone()[0]
        }

db = Database()

# ===== HELPER =====
def get_msg(update: Update):
    if update.callback_query and update.callback_query.message:
        return update.callback_query.message
    return update.message

# ===== PHONE CHECK =====
def check_phone(phone):
    phone = re.sub(r'[\s\-\(\)]', '', phone.strip())
    if phone.startswith('0'):
        phone = '+84' + phone[1:]
    elif not phone.startswith('+'):
        phone = '+84' + phone
    try:
        r = requests.get('http://apilayer.net/api/validate', params={
            'access_key': API_KEYS['numverify'], 'number': phone, 'format': 1}, timeout=10)
        d = r.json()
        return {
            'success': d.get('valid', False),
            'phone': d.get('international_format', phone),
            'country': d.get('country_name', 'Unknown'),
            'carrier': d.get('carrier', 'Unknown'),
            'line_type': d.get('line_type', 'Unknown'),
            'provider': 'Numverify'
        }
    except:
        return {'success': False, 'error': 'API error'}

# ===== BOT HANDLERS =====
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    msg = get_msg(update)
    if not msg:
        return
    db.create_user(u.id, u.username, u.full_name)
    bal = db.get_balance(u.id)
    is_ad = u.id in ADMIN_IDS
    
    text = f"👋 *{u.full_name}*\n\n📱 *Bot Check SĐT*\n💰 Số dư: *{bal:,}đ*\n💵 {PRICE_PER_CHECK:,}đ/lần\n\n📝 Gửi SĐT | /phone <số> | /nap"
    
    kb = [
        [InlineKeyboardButton("💵 Nạp tiền", callback_data="menu_nap"),
         InlineKeyboardButton("💰 Số dư", callback_data="menu_balance")]
    ]
    if is_ad:
        kb.append([InlineKeyboardButton("👑 Admin", callback_data="admin_panel")])
    
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

async def phone_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = get_msg(update)
    if not msg:
        return
    if not context.args:
        await msg.reply_text("📝 `/phone <số>`\nVD: `/phone 0912345678`", parse_mode=ParseMode.MARKDOWN)
        return
    update.message.text = ' '.join(context.args)
    await check_handler(update, context)

async def check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message
    if not msg:
        return
    
    phone = msg.text.strip()
    cleaned = re.sub(r'[\s\-\(\)]', '', phone)
    if not re.match(r'^\+?\d{7,15}$', cleaned):
        await msg.reply_text("❌ SĐT không hợp lệ!\nVD: 0912345678")
        return
    
    bal = db.get_balance(uid)
    if bal < PRICE_PER_CHECK:
        await msg.reply_text(f"❌ Không đủ tiền!\n💰 {bal:,}đ / 💵 {PRICE_PER_CHECK:,}đ\n/nap để nạp")
        return
    
    p = await msg.reply_text("🔄 Đang kiểm tra...")
    result = check_phone(phone)
    db.update_balance(uid, -PRICE_PER_CHECK)
    
    if result.get('success'):
        text = (
            f"✅ *KẾT QUẢ*\n"
            f"📞 `{result['phone']}`\n"
            f"🌍 {result['country']}\n"
            f"📡 {result['carrier']}\n"
            f"📋 {result['line_type']}\n"
            f"💵 {PRICE_PER_CHECK:,}đ | 💰 {db.get_balance(uid):,}đ"
        )
    else:
        db.update_balance(uid, PRICE_PER_CHECK)
        text = f"❌ Thất bại!\n💵 Đã hoàn {PRICE_PER_CHECK:,}đ\n💰 {db.get_balance(uid):,}đ"
    
    await p.edit_text(text, parse_mode=ParseMode.MARKDOWN)

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = get_msg(update)
    if not msg:
        return
    u = db.get_user(uid)
    bal = db.get_balance(uid)
    await msg.reply_text(
        f"💰 *TÀI KHOẢN*\n💵 {bal:,}đ\n📊 Check: {u['total_checks']}\n💸 Chi: {u['total_spent']:,}đ\n💳 Nạp: {u['total_deposited']:,}đ",
        parse_mode=ParseMode.MARKDOWN
    )

async def nap_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = get_msg(update)
    if not msg:
        return
    
    text = (
        "💵 *NẠP TIỀN*\n\n"
        f"🏦 *{BANK_INFO['bank']}*\n"
        f"💳 `{BANK_INFO['account']}`\n"
        f"👤 {BANK_INFO['name']}\n"
        f"📩 CK: `NAP {uid}`\n\n"
        "Sau khi CK, dùng /naptien <số_tiền>"
    )
    
    kb = []
    row = []
    for pkg in PACKAGES:
        row.append(InlineKeyboardButton(f"{pkg['emoji']} {pkg['amount']:,}đ", callback_data=f"nap_{pkg['amount']}"))
        if len(row) == 3:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

async def naptien_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = get_msg(update)
    if not msg:
        return
    
    if not context.args:
        await msg.reply_text("📝 `/naptien <số_tiền>`\nVD: `/naptien 50000`", parse_mode=ParseMode.MARKDOWN)
        return
    
    try:
        amount = int(context.args[0])
        if amount < 10000:
            await msg.reply_text("❌ Tối thiểu 10,000đ!")
            return
        
        rid = db.create_request(uid, amount)
        await msg.reply_text(
            f"✅ *Yêu cầu #{rid}*\n💵 {amount:,}đ\n⏳ Chờ admin duyệt...",
            parse_mode=ParseMode.MARKDOWN
        )
        
        for aid in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    aid,
                    f"🔔 Yêu cầu #{rid}\n👤 `{uid}`\n💵 {amount:,}đ\n/duyet {rid}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
    except:
        await msg.reply_text("❌ Số không hợp lệ!")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = get_msg(update)
    if not msg:
        return
    await msg.reply_text(
        "📖 *HƯỚNG DẪN*\n\n"
        "/phone <số> - Kiểm tra SĐT\n"
        "/nap - Nạp tiền\n"
        "/naptien <tiền> - Gửi yêu cầu nạp\n"
        "/balance - Xem số dư\n\n"
        f"🏦 {BANK_INFO['bank']}: `{BANK_INFO['account']}`",
        parse_mode=ParseMode.MARKDOWN
    )

# ===== ADMIN HANDLERS =====
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    msg = get_msg(update)
    if not msg:
        return
    s = db.get_stats()
    await msg.reply_text(
        f"👑 *ADMIN*\n👥 Users: {s['users']}\n⏳ Pending: {s['pending']}\n\n/duyet <id> | /huy <id>",
        parse_mode=ParseMode.MARKDOWN
    )

async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    msg = get_msg(update)
    if not msg:
        return
    
    pending = db.get_pending()
    if not pending:
        await msg.reply_text("✅ Không có yêu cầu chờ!")
        return
    
    text = f"📋 *YÊU CẦU CHỜ* ({len(pending)})\n\n"
    for r in pending:
        text += f"🆔 #{r['id']} | 👤 `{r['user_id']}` | 💵 {r['amount']:,}đ\n/duyet {r['id']} | /huy {r['id']}\n\n"
    
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def duyet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    msg = get_msg(update)
    if not msg or not context.args:
        return
    
    try:
        rid = int(context.args[0])
        ok, data = db.approve(rid, update.effective_user.id)
        if ok:
            await msg.reply_text(f"✅ Duyệt #{rid} - {data['amount']:,}đ")
            try:
                await context.bot.send_message(
                    data['user_id'],
                    f"✅ *Nạp tiền thành công!*\n💵 +{data['amount']:,}đ\n💰 Dư: {db.get_balance(data['user_id']):,}đ",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        else:
            await msg.reply_text("❌ Không tìm thấy!")
    except:
        pass

async def huy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    msg = get_msg(update)
    if not msg or not context.args:
        return
    
    try:
        rid = int(context.args[0])
        db.reject(rid, update.effective_user.id)
        await msg.reply_text(f"🚫 Hủy #{rid}")
    except:
        pass

# ===== CALLBACK =====
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    d = q.data
    uid = update.effective_user.id
    msg = q.message
    if not msg:
        return
    
    if d == "menu_nap":
        await nap_cmd(update, context)
    elif d == "menu_balance":
        await balance_cmd(update, context)
    elif d == "admin_panel" and uid in ADMIN_IDS:
        await admin_cmd(update, context)
    elif d.startswith("nap_"):
        amount = int(d.replace("nap_", ""))
        rid = db.create_request(uid, amount)
        await msg.reply_text(
            f"✅ *Yêu cầu #{rid}*\n💵 {amount:,}đ\n⏳ Chờ duyệt...\n\n"
            f"🏦 {BANK_INFO['bank']}\n💳 `{BANK_INFO['account']}`\n📩 `NAP {uid}`",
            parse_mode=ParseMode.MARKDOWN
        )
        for aid in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    aid,
                    f"🔔 Yêu cầu #{rid}\n👤 `{uid}`\n💵 {amount:,}đ\n/duyet {rid}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass

# ===== MAIN =====
if __name__ == '__main__':
    print("=" * 50)
    print("🤖 BOT KIỂM TRA SĐT - RAILWAY")
    print(f"👑 Admin: {ADMIN_IDS}")
    print(f"🐍 Python: {sys.version}")
    print("=" * 50)
    
    # Fix event loop cho Windows
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Tạo app
    app = Application.builder().token(BOT_TOKEN).build()
    
    # User handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("phone", phone_cmd))
    app.add_handler(CommandHandler("nap", nap_cmd))
    app.add_handler(CommandHandler("naptien", naptien_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    
    # Admin handlers
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("pending", pending_cmd))
    app.add_handler(CommandHandler("duyet", duyet_cmd))
    app.add_handler(CommandHandler("huy", huy_cmd))
    
    # Callback + Message
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_handler))
    
    print("✅ Bot ready!")
    
    # Chạy bot
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
