#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Bot Telegram Kiểm Tra Số Điện Thoại - Nạp tiền trực tiếp
Author: @trangiatien33
Version: 8.0 - Fix lỗi /phone + Render deploy
"""

import logging
import time
import json
import sqlite3
import re
import os
from datetime import datetime

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ===== LOGGING =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
    {'name': 'Gói Cơ Bản', 'amount': 10000, 'checks': 2, 'emoji': '🥉'},
    {'name': 'Gói Tiết Kiệm', 'amount': 20000, 'checks': 4, 'emoji': '🥈'},
    {'name': 'Gói Phổ Thông', 'amount': 50000, 'checks': 10, 'emoji': '🥇'},
    {'name': 'Gói Cao Cấp', 'amount': 100000, 'checks': 20, 'emoji': '💎'},
    {'name': 'Gói VIP', 'amount': 200000, 'checks': 40, 'emoji': '👑'},
    {'name': 'Gói Đại Gia', 'amount': 500000, 'checks': 100, 'emoji': '🌟'}
]

PRICE_PER_CHECK = 5000

TYPE_MAP = {
    'mobile': '📱 Di động',
    'fixed_line': '📞 Cố định',
    'fixed_line_or_mobile': '📞📱 Cố định/Di động',
    'toll_free': '🆓 Miễn phí',
    'premium_rate': '💎 Tính phí cao',
    'voip': '🌐 VoIP',
    'personal': '👤 Cá nhân',
    'pager': '📟 Pager',
    'uan': '🏢 UAN'
}

# ===== HELPER =====
def get_message(update: Update):
    """Lấy message object từ update"""
    if hasattr(update, 'callback_query') and update.callback_query:
        return update.callback_query.message
    return update.message

# ===== DATABASE =====
class Database:
    def __init__(self, db_path="/tmp/phone_bot.db"):
        # Dùng /tmp cho Render, dùng local cho Termux
        if os.path.exists("/data/data/com.termux"):
            db_path = "phone_bot.db"
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.create_tables()
        self.init_admin()

    def _row_to_dict(self, row):
        if row is None:
            return None
        return {key: row[key] for key in row.keys()}

    def create_tables(self):
        c = self.conn.cursor()
        
        try:
            c.execute("SELECT package_name FROM deposit_requests LIMIT 1")
        except:
            c.execute("DROP TABLE IF EXISTS deposit_requests")
        
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
            balance REAL DEFAULT 0, total_checks INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0,
            total_deposited REAL DEFAULT 0, total_spent REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT,
            amount REAL, balance_before REAL, balance_after REAL,
            description TEXT, admin_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS check_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            phone_number TEXT, result TEXT, cost REAL, status TEXT,
            api_provider TEXT, response_time REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS deposit_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            amount REAL, package_name TEXT DEFAULT '', status TEXT DEFAULT 'pending',
            admin_id INTEGER, note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP)''')
        
        self.conn.commit()

    def init_admin(self):
        c = self.conn.cursor()
        for aid in ADMIN_IDS:
            c.execute('INSERT OR IGNORE INTO users (user_id, is_admin, balance) VALUES (?, 1, 999999)', (aid,))
        self.conn.commit()

    def get_user(self, uid):
        c = self.conn.cursor()
        c.execute('SELECT * FROM users WHERE user_id = ?', (uid,))
        return self._row_to_dict(c.fetchone())

    def create_user(self, uid, username=None, full_name=None):
        c = self.conn.cursor()
        is_ad = 1 if uid in ADMIN_IDS else 0
        bal = 999999 if is_ad else 0
        c.execute('INSERT OR IGNORE INTO users (user_id, username, full_name, is_admin, balance) VALUES (?,?,?,?,?)',
                  (uid, username, full_name, is_ad, bal))
        self.conn.commit()

    def get_balance(self, uid):
        c = self.conn.cursor()
        c.execute('SELECT balance FROM users WHERE user_id = ?', (uid,))
        r = c.fetchone()
        return r[0] if r else 0

    def update_balance(self, uid, amount, ttype="", desc="", admin_id=None):
        c = self.conn.cursor()
        c.execute('SELECT balance FROM users WHERE user_id = ?', (uid,))
        r = c.fetchone()
        cur = r[0] if r else 0
        new = cur + amount
        if new < 0:
            return False, cur
        c.execute('UPDATE users SET balance = ?, last_active_at = CURRENT_TIMESTAMP WHERE user_id = ?', (new, uid))
        if amount > 0:
            c.execute('UPDATE users SET total_deposited = total_deposited + ? WHERE user_id = ?', (amount, uid))
        else:
            c.execute('UPDATE users SET total_spent = total_spent + ? WHERE user_id = ?', (abs(amount), uid))
        if ttype:
            c.execute('INSERT INTO transactions (user_id, type, amount, balance_before, balance_after, description, admin_id) VALUES (?,?,?,?,?,?,?)',
                      (uid, ttype, amount, cur, new, desc, admin_id))
        self.conn.commit()
        return True, new

    def create_deposit_request(self, uid, amount, package_name=""):
        c = self.conn.cursor()
        c.execute('INSERT INTO deposit_requests (user_id, amount, package_name) VALUES (?,?,?)',
                  (uid, amount, package_name))
        self.conn.commit()
        return c.lastrowid

    def get_pending_requests(self):
        c = self.conn.cursor()
        c.execute('SELECT * FROM deposit_requests WHERE status="pending" ORDER BY created_at DESC')
        return [self._row_to_dict(r) for r in c.fetchall()]

    def get_pending_by_user(self, uid):
        c = self.conn.cursor()
        c.execute('SELECT * FROM deposit_requests WHERE user_id=? AND status="pending" ORDER BY created_at DESC', (uid,))
        return [self._row_to_dict(r) for r in c.fetchall()]

    def approve_deposit(self, request_id, admin_id):
        c = self.conn.cursor()
        c.execute('SELECT * FROM deposit_requests WHERE id=? AND status="pending"', (request_id,))
        r = c.fetchone()
        if not r:
            return False, None
        d = self._row_to_dict(r)
        c.execute('UPDATE deposit_requests SET status="approved", admin_id=?, processed_at=CURRENT_TIMESTAMP WHERE id=?',
                  (admin_id, request_id))
        self.update_balance(d['user_id'], d['amount'], 'deposit',
                          f'Nạp {d["amount"]:,}đ - {d.get("package_name", "")}', admin_id)
        self.conn.commit()
        return True, d

    def reject_deposit(self, request_id, admin_id):
        c = self.conn.cursor()
        c.execute('UPDATE deposit_requests SET status="rejected", admin_id=?, processed_at=CURRENT_TIMESTAMP WHERE id=? AND status="pending"',
                  (admin_id, request_id))
        self.conn.commit()

    def save_check_history(self, uid, phone, result, cost, status, provider, rt):
        c = self.conn.cursor()
        c.execute('INSERT INTO check_history (user_id, phone_number, result, cost, status, api_provider, response_time) VALUES (?,?,?,?,?,?,?)',
                  (uid, phone, json.dumps(result), cost, status, provider, rt))
        c.execute('UPDATE users SET total_checks = total_checks + 1 WHERE user_id = ?', (uid,))
        self.conn.commit()

    def get_stats(self):
        c = self.conn.cursor()
        c.execute('SELECT COUNT(*) as c FROM users'); tu = c.fetchone()['c']
        c.execute('SELECT SUM(balance) as t FROM users'); tb = c.fetchone()['t'] or 0
        c.execute('SELECT SUM(amount) as t FROM transactions WHERE type="deposit" AND amount>0'); tr = c.fetchone()['t'] or 0
        c.execute('SELECT COUNT(*) as c FROM check_history'); tc = c.fetchone()['c']
        c.execute('SELECT COUNT(*) as c FROM deposit_requests WHERE status="pending"'); tp = c.fetchone()['c']
        return {'total_users': tu, 'total_balance': tb, 'total_revenue': tr, 'total_checks': tc, 'pending': tp}

# ===== PHONE CHECKER =====
class PhoneChecker:
    @staticmethod
    def check_numverify(phone):
        try:
            phone = phone.strip().replace(' ','').replace('-','')
            if phone.startswith('0'): phone = '+84' + phone[1:]
            elif not phone.startswith('+'): phone = '+' + phone if phone.startswith('84') else '+84' + phone
            start = time.time()
            r = requests.get('http://apilayer.net/api/validate', params={
                'access_key': API_KEYS['numverify'], 'number': phone, 'format': 1
            }, timeout=10)
            rt = time.time() - start
            d = r.json()
            if r.status_code == 200:
                return {
                    'success': d.get('valid', False),
                    'phone': d.get('international_format', phone),
                    'local_format': d.get('local_format', phone),
                    'country': d.get('country_name', 'Unknown'),
                    'country_code': d.get('country_code', ''),
                    'location': d.get('location', ''),
                    'carrier': d.get('carrier', 'Unknown'),
                    'line_type': d.get('line_type', 'Unknown'),
                    'provider': 'Numverify',
                    'response_time': round(rt, 2)
                }
            return {'success': False, 'error': 'API error', 'provider': 'Numverify'}
        except:
            return {'success': False, 'error': 'Connection error', 'provider': 'Numverify'}

    @staticmethod
    def check_abstractapi(phone):
        try:
            phone = phone.strip().replace(' ','').replace('-','')
            if phone.startswith('0'): phone = '+84' + phone[1:]
            elif not phone.startswith('+'): phone = '+' + phone if phone.startswith('84') else '+84' + phone
            start = time.time()
            r = requests.get('https://phonevalidation.abstractapi.com/v1/', params={
                'api_key': API_KEYS['abstract'], 'phone': phone
            }, timeout=10)
            rt = time.time() - start
            d = r.json()
            return {
                'success': d.get('valid', False),
                'phone': d.get('format', {}).get('international', phone),
                'local_format': d.get('format', {}).get('local', phone),
                'country': d.get('country', {}).get('name', 'Unknown'),
                'country_code': d.get('country', {}).get('code', ''),
                'carrier': d.get('carrier', 'Unknown'),
                'line_type': d.get('line_type', 'Unknown'),
                'provider': 'AbstractAPI',
                'response_time': round(rt, 2)
            }
        except:
            return {'success': False, 'error': 'Connection error', 'provider': 'AbstractAPI'}

    @staticmethod
    def check_veriphone(phone):
        try:
            phone = phone.strip().replace(' ','').replace('-','')
            if phone.startswith('0'): phone = '+84' + phone[1:]
            elif not phone.startswith('+'): phone = '+' + phone if phone.startswith('84') else '+84' + phone
            start = time.time()
            r = requests.get('https://api.veriphone.io/v2/verify', params={
                'key': API_KEYS['veriphone'], 'phone': phone
            }, timeout=10)
            rt = time.time() - start
            d = r.json()
            return {
                'success': d.get('phone_valid', False),
                'phone': d.get('international_number', phone),
                'local_format': d.get('local_number', phone),
                'country': d.get('country', 'Unknown'),
                'country_code': d.get('country_code', ''),
                'carrier': d.get('carrier', 'Unknown'),
                'phone_type': d.get('phone_type', 'Unknown'),
                'provider': 'Veriphone',
                'response_time': round(rt, 2)
            }
        except:
            return {'success': False, 'error': 'Connection error', 'provider': 'Veriphone'}

    @staticmethod
    def check_phone(phone):
        for check in [PhoneChecker.check_numverify, PhoneChecker.check_abstractapi, PhoneChecker.check_veriphone]:
            result = check(phone)
            if result.get('success'):
                return result
        return result

# ===== DATABASE INSTANCE =====
db = Database()

# ===== DECORATORS =====
def admin_only(func):
    async def wrapper(update, context):
        if update.effective_user.id not in ADMIN_IDS:
            await get_message(update).reply_text("🚫 Chỉ Admin mới dùng được!", parse_mode='Markdown')
            return
        return await func(update, context)
    return wrapper

# ===== PROCESS PHONE CHECK (DÙNG CHUNG) =====
async def process_phone_check(update, context, phone):
    """Xử lý kiểm tra SĐT - dùng chung cho gửi trực tiếp và lệnh /phone"""
    uid = update.effective_user.id
    msg = update.message if update.message else get_message(update)
    
    cleaned = re.sub(r'[\s\-\(\)]', '', phone)
    if not re.match(r'^\+?\d{7,15}$', cleaned):
        await msg.reply_text(
            "❌ *SĐT không hợp lệ!*\n\n"
            "📝 *Ví dụ đúng:*\n"
            "• `0912345678`\n"
            "• `+84912345678`\n"
            "• `+14155552671`",
            parse_mode='Markdown'
        )
        return
    
    bal = db.get_balance(uid)
    if bal < PRICE_PER_CHECK:
        await msg.reply_text(
            f"❌ *Không đủ số dư!*\n\n"
            f"💰 Số dư: *{bal:,}đ*\n"
            f"💵 Cần: *{PRICE_PER_CHECK:,}đ*\n"
            f"⚠️ Thiếu: *{PRICE_PER_CHECK-bal:,}đ*\n\n"
            "Dùng /nap để nạp thêm tiền",
            parse_mode='Markdown'
        )
        return
    
    p = await msg.reply_text("🔄 *Đang kiểm tra...*", parse_mode='Markdown')
    result = PhoneChecker.check_phone(phone)
    
    db.update_balance(uid, -PRICE_PER_CHECK, 'check', f'Check {phone}')
    db.save_check_history(uid, phone, result, PRICE_PER_CHECK,
                         'success' if result.get('success') else 'failed',
                         result.get('provider', '?'),
                         result.get('response_time', 0))
    
    if result.get('success'):
        phone_display = result.get('phone', phone)
        local_display = result.get('local_format', '')
        country = result.get('country', 'Unknown')
        country_code = result.get('country_code', '')
        carrier_name = result.get('carrier', 'Unknown')
        line_type = result.get('line_type') or result.get('phone_type', 'unknown')
        type_display = TYPE_MAP.get(line_type, f'📋 {line_type}')
        location = result.get('location', '')
        provider = result.get('provider', 'Unknown')
        rt = result.get('response_time', 0)
        now = datetime.now().strftime('%H:%M:%S %d/%m/%Y')
        
        nums = phone_display.replace('+', '').replace(' ', '')
        cc = nums[:2] if nums.startswith('84') else nums[:1]
        rest = nums[len(cc):]
        if len(rest) >= 9:
            international = f"+{cc} {rest[:3]} {rest[3:6]} {rest[6:]}"
            local = f"0{rest[:3]} {rest[3:6]} {rest[6:]}"
        else:
            international = phone_display
            local = local_display
        
        text = (
            f"✅ *KẾT QUẢ KIỂM TRA SỐ ĐIỆN THOẠI*\n"
            f"{'─' * 30}\n\n"
            f"📞 *SĐT:* `{phone_display}`\n"
            f"🌐 *Quốc tế:* `{international}`\n"
            f"🇻🇳 *Nội địa:* `{local}`\n\n"
            f"✅ *Hợp lệ:* Có\n"
            f"📋 *Loại:* {type_display}\n"
            f"📡 *Nhà mạng:* *{carrier_name}*\n"
            f"🌍 *Quốc gia:* {country} ({country_code})\n"
        )
        if location:
            text += f"📍 *Khu vực:* {location}\n"
        text += (
            f"\n🔌 *API:* {provider} | ⚡ {rt}s\n"
            f"💵 *Phí:* {PRICE_PER_CHECK:,}đ\n"
            f"💰 *Số dư:* {db.get_balance(uid):,}đ\n\n"
            f"⏰ *Tra cứu lúc:* {now}"
        )
    else:
        db.update_balance(uid, PRICE_PER_CHECK, 'refund', 'Hoàn tiền')
        text = (
            f"❌ *KIỂM TRA THẤT BẠI*\n\n"
            f"📞 SĐT: `{phone}`\n"
            f"⚠️ Lỗi: {result.get('error', 'Unknown')}\n"
            f"💵 *Đã hoàn:* {PRICE_PER_CHECK:,}đ\n"
            f"💰 *Số dư:* {db.get_balance(uid):,}đ"
        )
    
    await p.edit_text(text, parse_mode='Markdown')

# ===== USER HANDLERS =====
async def start(update, context):
    user = update.effective_user
    msg = get_message(update)
    db.create_user(user.id, user.username, user.full_name)
    is_ad = user.id in ADMIN_IDS
    bal = db.get_balance(user.id)
    
    text = (
        f"👋 *Xin chào {user.full_name}!*\n\n"
        f"📱 *Bot Kiểm Tra Số Điện Thoại*\n"
        f"🔄 3 API Quốc tế | 💵 {PRICE_PER_CHECK:,}đ/lần\n\n"
    )
    if is_ad:
        text += "👑 *Bạn là Admin*\n\n"
    
    text += (
        f"💰 *Số dư:* {bal:,}đ\n"
        f"📊 *Có thể check:* {int(bal/PRICE_PER_CHECK)} lần\n\n"
        "📝 *Cách dùng:*\n"
        "• Gửi SĐT để kiểm tra\n"
        "• /phone <SĐT> - Kiểm tra nhanh\n"
        "• /nap - Nạp tiền vào TK\n"
        "• /balance - Xem số dư\n"
        "• /help - Hướng dẫn chi tiết"
    )
    
    kb = [
        [InlineKeyboardButton("💵 Nạp tiền", callback_data="menu_nap"),
         InlineKeyboardButton("💰 Số dư", callback_data="menu_balance")],
        [InlineKeyboardButton("📋 Bảng giá", callback_data="menu_prices"),
         InlineKeyboardButton("ℹ️ Hỗ trợ", callback_data="menu_help")]
    ]
    if is_ad:
        kb.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    
    await msg.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

async def phone_command(update, context):
    """Lệnh /phone <số> - Kiểm tra nhanh"""
    msg = get_message(update)
    
    if not context.args:
        await msg.reply_text(
            "📝 *Cách dùng:* `/phone <số điện thoại>`\n\n"
            "📌 *Ví dụ:*\n"
            "• `/phone 0912345678`\n"
            "• `/phone +84912345678`\n"
            "• `/phone +14155552671`",
            parse_mode='Markdown'
        )
        return
    
    phone = ' '.join(context.args)
    await process_phone_check(update, context, phone)

async def handle_phone_check(update, context):
    """Xử lý khi user gửi số điện thoại trực tiếp"""
    phone = update.message.text.strip()
    await process_phone_check(update, context, phone)

async def nap_command(update, context):
    msg = get_message(update)
    text = "💵 *NẠP TIỀN VÀO TÀI KHOẢN*\n\n📌 *Chọn gói nạp bên dưới:*\n\n"
    for pkg in PACKAGES:
        text += f"{pkg['emoji']} *{pkg['name']}*: {pkg['amount']:,}đ = {pkg['checks']} lần check\n"
    text += "\n💡 Sau khi chọn gói, bạn sẽ nhận được thông tin chuyển khoản."
    
    kb = []
    row = []
    for i, pkg in enumerate(PACKAGES):
        row.append(InlineKeyboardButton(
            f"{pkg['emoji']} {pkg['name']} - {pkg['amount']:,}đ",
            callback_data=f"select_pkg_{i}"
        ))
        if len(row) == 2 or i == len(PACKAGES) - 1:
            kb.append(row)
            row = []
    
    await msg.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

async def balance_command(update, context):
    uid = update.effective_user.id
    msg = get_message(update)
    u = db.get_user(uid)
    bal = db.get_balance(uid)
    
    text = (
        "💰 *THÔNG TIN TÀI KHOẢN*\n\n"
        f"💵 *Số dư:* {bal:,}đ\n"
        f"📊 *Đã check:* {u['total_checks']} lần\n"
        f"💸 *Đã chi:* {u['total_spent']:,}đ\n"
        f"💳 *Đã nạp:* {u['total_deposited']:,}đ\n\n"
        f"🎯 *Có thể check thêm:* {int(bal/PRICE_PER_CHECK)} lần"
    )
    
    kb = [[InlineKeyboardButton("💵 Nạp thêm", callback_data="menu_nap")]]
    await msg.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

async def help_command(update, context):
    await get_message(update).reply_text(
        "📖 *HƯỚNG DẪN SỬ DỤNG*\n\n"
        "📱 *Kiểm tra SĐT:*\n"
        "• Gửi trực tiếp số điện thoại\n"
        "• Dùng lệnh: `/phone <SĐT>`\n\n"
        "💵 *Nạp tiền:* /nap → Chọn gói → CK\n"
        "💰 *Số dư:* /balance\n\n"
        f"🏦 *Ngân hàng:* {BANK_INFO['bank']}\n"
        f"📞 *STK:* `{BANK_INFO['account']}`\n"
        f"👤 *Chủ TK:* {BANK_INFO['name']}\n\n"
        f"🛒 *Hỗ trợ:* {ADMIN_USERNAME}",
        parse_mode='Markdown'
    )

# ===== ADMIN HANDLERS =====
@admin_only
async def admin_command(update, context):
    msg = get_message(update)
    stats = db.get_stats()
    
    text = (
        "👑 *ADMIN PANEL*\n\n"
        f"👥 Users: *{stats['total_users']}*\n"
        f"💰 Tổng dư: *{stats['total_balance']:,}đ*\n"
        f"💵 Doanh thu: *{stats['total_revenue']:,}đ*\n"
        f"📊 Lượt check: *{stats['total_checks']}*\n"
        f"⏳ Chờ duyệt: *{stats['pending']}*\n\n"
        "/pending - Xem yêu cầu chờ\n"
        "/duyet <id> - Duyệt nạp tiền\n"
        "/huy <id> - Hủy yêu cầu\n"
        "/addbalance <uid> <tiền> - Cộng tiền"
    )
    
    kb = [
        [InlineKeyboardButton("📋 Yêu cầu chờ", callback_data="admin_pending"),
         InlineKeyboardButton("📊 Thống kê", callback_data="admin_stats")],
        [InlineKeyboardButton("💰 Cộng tiền", callback_data="admin_addbal")]
    ]
    await msg.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

@admin_only
async def admin_pending(update, context):
    msg = get_message(update)
    pending = db.get_pending_requests()
    
    if not pending:
        await msg.reply_text("✅ *Không có yêu cầu nào chờ duyệt!*", parse_mode='Markdown')
        return
    
    text = f"📋 *YÊU CẦU CHỜ DUYỆT* ({len(pending)})\n\n"
    for r in pending:
        text += (
            f"🆔 `#{r['id']}` | 👤 `{r['user_id']}`\n"
            f"📦 {r.get('package_name', 'N/A')} | 💵 *{r['amount']:,}đ*\n"
            f"⏰ {r['created_at'][:19]}\n"
            f"✅ `/duyet {r['id']}` | ❌ `/huy {r['id']}`\n\n"
        )
    
    kb = []
    for r in pending[:6]:
        kb.append([InlineKeyboardButton(
            f"✅ Duyệt #{r['id']} - {r['amount']:,}đ",
            callback_data=f"approve_{r['id']}"
        )])
    
    await msg.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

@admin_only
async def admin_duyet(update, context):
    msg = get_message(update)
    if not context.args:
        await msg.reply_text("📝 `/duyet <id_yêu_cầu>`", parse_mode='Markdown')
        return
    try:
        req_id = int(context.args[0])
        ok, data = db.approve_deposit(req_id, update.effective_user.id)
        if ok:
            await msg.reply_text(
                f"✅ *Đã duyệt #{req_id}*\n"
                f"👤 `{data['user_id']}` | 💵 *{data['amount']:,}đ*\n"
                f"📦 {data.get('package_name', 'N/A')}",
                parse_mode='Markdown'
            )
            try:
                await context.bot.send_message(
                    data['user_id'],
                    f"✅ *NẠP TIỀN THÀNH CÔNG!*\n\n"
                    f"💵 +{data['amount']:,}đ\n"
                    f"💰 Dư: *{db.get_balance(data['user_id']):,}đ*\n\n"
                    "📱 Gửi SĐT để kiểm tra ngay!",
                    parse_mode='Markdown'
                )
            except: pass
        else:
            await msg.reply_text(f"❌ Yêu cầu #{req_id} không tồn tại!")
    except:
        await msg.reply_text("❌ Lỗi!")

@admin_only
async def admin_huy(update, context):
    msg = get_message(update)
    if not context.args:
        await msg.reply_text("📝 `/huy <id_yêu_cầu>`", parse_mode='Markdown')
        return
    try:
        req_id = int(context.args[0])
        db.reject_deposit(req_id, update.effective_user.id)
        await msg.reply_text(f"🚫 Đã hủy #{req_id}")
    except:
        await msg.reply_text("❌ Lỗi!")

@admin_only
async def admin_add_balance(update, context):
    msg = get_message(update)
    if len(context.args) < 2:
        await msg.reply_text("📝 `/addbalance <uid> <tiền>`\nVD: `/addbalance 123456 50000`", parse_mode='Markdown')
        return
    try:
        tid = int(context.args[0]); amt = int(context.args[1])
        db.create_user(tid)
        ok, nb = db.update_balance(tid, amt, 'deposit', 'Admin cộng tiền', update.effective_user.id)
        if ok:
            await msg.reply_text(f"✅ Đã cộng *{amt:,}đ* cho `{tid}`\n💰 Dư: *{nb:,}đ*", parse_mode='Markdown')
        else:
            await msg.reply_text("❌ Thất bại!")
    except:
        await msg.reply_text("❌ Lỗi cú pháp!")

# ===== CALLBACKS =====
async def button_handler(update, context):
    q = update.callback_query
    await q.answer()
    d = q.data; uid = update.effective_user.id; msg = q.message
    
    if d == "menu_nap":
        await nap_command(update, context)
    elif d == "menu_balance":
        await balance_command(update, context)
    elif d == "menu_prices":
        text = "📋 *BẢNG GIÁ*\n\n"
        for pkg in PACKAGES:
            text += f"{pkg['emoji']} *{pkg['name']}*: {pkg['amount']:,}đ = {pkg['checks']} lần\n"
        text += f"\n💵 *1 lần check:* {PRICE_PER_CHECK:,}đ\n🔄 Fail → Hoàn tiền 100%"
        await msg.reply_text(text, parse_mode='Markdown')
    elif d == "menu_help":
        await help_command(update, context)
    elif d.startswith("select_pkg_"):
        idx = int(d.replace("select_pkg_", ""))
        if idx < len(PACKAGES):
            await show_payment_form(update, context, PACKAGES[idx], uid)
    elif d.startswith("confirm_payment_"):
        parts = d.replace("confirm_payment_", "").split("_")
        pkg_idx = int(parts[0])
        if pkg_idx < len(PACKAGES):
            pkg = PACKAGES[pkg_idx]
            pending = db.get_pending_by_user(uid)
            if pending:
                await msg.reply_text("⚠️ *Bạn đã có yêu cầu đang chờ!*", parse_mode='Markdown')
            else:
                req_id = db.create_deposit_request(uid, pkg['amount'], pkg['name'])
                await msg.reply_text(
                    f"✅ *ĐÃ GỬI YÊU CẦU*\n\n"
                    f"🆔 `#{req_id}` | 📦 {pkg['name']}\n"
                    f"💵 *{pkg['amount']:,}đ* | ⏳ Chờ duyệt\n"
                    f"⏰ 1-5 phút",
                    parse_mode='Markdown'
                )
                for aid in ADMIN_IDS:
                    try:
                        await context.bot.send_message(aid,
                            f"🔔 *Yêu cầu mới #{req_id}*\n👤 `{uid}` | 📦 {pkg['name']}\n💵 *{pkg['amount']:,}đ*\n/duyet {req_id}",
                            parse_mode='Markdown')
                    except: pass
    elif d == "admin_panel" and uid in ADMIN_IDS:
        await admin_command(update, context)
    elif d == "admin_pending" and uid in ADMIN_IDS:
        await admin_pending(update, context)
    elif d == "admin_stats" and uid in ADMIN_IDS:
        s = db.get_stats()
        await msg.reply_text(f"📊 *STATS*\n👥 {s['total_users']}\n💰 {s['total_balance']:,}đ\n💵 {s['total_revenue']:,}đ\n📊 {s['total_checks']}\n⏳ {s['pending']}", parse_mode='Markdown')
    elif d == "admin_addbal" and uid in ADMIN_IDS:
        await msg.reply_text("📝 `/addbalance <user_id> <số_tiền>`", parse_mode='Markdown')
    elif d.startswith("approve_") and uid in ADMIN_IDS:
        req_id = int(d.replace("approve_", ""))
        ok, data = db.approve_deposit(req_id, uid)
        if ok:
            await msg.edit_text(f"{msg.text}\n\n✅ Đã duyệt #{req_id}", parse_mode='Markdown')
            try:
                await context.bot.send_message(data['user_id'],
                    f"✅ *NẠP TIỀN THÀNH CÔNG!*\n💵 +{data['amount']:,}đ\n💰 Dư: {db.get_balance(data['user_id']):,}đ",
                    parse_mode='Markdown')
            except: pass
    elif d.startswith("add50k_") and uid in ADMIN_IDS:
        tid = int(d.split("_")[1]); db.create_user(tid)
        ok, _ = db.update_balance(tid, 50000, 'deposit', 'Admin thưởng', uid)
        if ok: await msg.reply_text(f"✅ +50k cho {tid}")
    elif d.startswith("add100k_") and uid in ADMIN_IDS:
        tid = int(d.split("_")[1]); db.create_user(tid)
        ok, _ = db.update_balance(tid, 100000, 'deposit', 'Admin thưởng', uid)
        if ok: await msg.reply_text(f"✅ +100k cho {tid}")

async def show_payment_form(update, context, pkg, uid):
    q = update.callback_query
    msg = q.message
    
    text = (
        "💰 *THÔNG TIN NẠP TIỀN*\n"
        f"{'─' * 30}\n\n"
        f"🏦 *Ngân hàng:* {BANK_INFO['bank']}\n"
        f"💳 *Số TK:* `{BANK_INFO['account']}`\n"
        f"👤 *Chủ TK:* {BANK_INFO['name']}\n\n"
        f"📦 *Gói:* {pkg['emoji']} {pkg['name']}\n"
        f"💵 *Số tiền:* *{pkg['amount']:,}đ*\n"
        f"🎯 *Lần check:* {pkg['checks']}\n\n"
        f"📩 *Nội dung CK:* `NAP {uid}`\n"
        f"   (Theo ID user của bạn)\n\n"
        "📌 *Chuyển đúng số tiền & nội dung.*\n"
        "Sau khi chuyển thành công, hãy nhấn nút\n"
        "*Xác nhận* bên dưới."
    )
    
    kb = [[InlineKeyboardButton("✅ XÁC NHẬN ĐÃ CHUYỂN KHOẢN", 
                                callback_data=f"confirm_payment_{PACKAGES.index(pkg)}")]]
    
    await msg.edit_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

# ===== ERROR HANDLER =====
async def error_handler(update, context):
    logger.error(f"Error: {context.error}", exc_info=True)
    try:
        if update:
            m = get_message(update)
            if m: await m.reply_text("❌ Lỗi! Thử lại sau.")
    except: pass

# ===== MAIN =====
async def setup_commands(app):
    await app.bot.set_my_commands([
        BotCommand("start", "🚀 Khởi động bot"),
        BotCommand("phone", "📱 Kiểm tra SĐT nhanh"),
        BotCommand("nap", "💵 Nạp tiền vào TK"),
        BotCommand("balance", "💰 Xem số dư"),
        BotCommand("help", "📖 Hướng dẫn")
    ])

def main():
    print("=" * 50)
    print("🤖 BOT KIỂM TRA SĐT - READY")
    print(f"👑 Admin: {ADMIN_IDS}")
    print(f"🏦 Bank: {BANK_INFO['bank']} - {BANK_INFO['account']}")
    print("=" * 50)
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.post_init = setup_commands
    
    # User commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("phone", phone_command))
    app.add_handler(CommandHandler("nap", nap_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("help", help_command))
    
    # Admin commands
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("pending", admin_pending))
    app.add_handler(CommandHandler("duyet", admin_duyet))
    app.add_handler(CommandHandler("huy", admin_huy))
    app.add_handler(CommandHandler("addbalance", admin_add_balance))
    
    # Callback
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Phone check (tin nhắn thường)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone_check))
    
    # Error
    app.add_error_handler(error_handler)
    
    # Setup commands
    app.post_init = setup_commands
    
    print("✅ Bot ready!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
