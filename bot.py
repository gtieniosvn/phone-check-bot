import os
import sys
import io
import asyncio
import logging
import time
import json
import sqlite3
import re
import random
from datetime import datetime
from io import BytesIO

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
    {'name': 'Gói Cơ Bản', 'amount': 10000, 'checks': 2, 'emoji': '🥉'},
    {'name': 'Gói Tiết Kiệm', 'amount': 20000, 'checks': 4, 'emoji': '🥈'},
    {'name': 'Gói Phổ Thông', 'amount': 50000, 'checks': 10, 'emoji': '🥇'},
    {'name': 'Gói Cao Cấp', 'amount': 100000, 'checks': 20, 'emoji': '💎'},
    {'name': 'Gói VIP', 'amount': 200000, 'checks': 40, 'emoji': '👑'},
    {'name': 'Gói Đại Gia', 'amount': 500000, 'checks': 100, 'emoji': '🌟'}
]
PRICE_PER_CHECK = 5000

# ===== LOGGING =====
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ===== DATABASE =====
class Database:
    def __init__(self):
        self.conn = sqlite3.connect("phone_bot.db", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._setup()

    def _row(self, row):
        if row is None: return None
        return {k: row[k] for k in row.keys()}

    def _setup(self):
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
            balance REAL DEFAULT 0, total_checks INTEGER DEFAULT 0,
            total_deposited REAL DEFAULT 0, total_spent REAL DEFAULT 0,
            is_banned INTEGER DEFAULT 0, language TEXT DEFAULT 'vi',
            ai_chats INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS deposit_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            amount REAL, package_name TEXT DEFAULT '', status TEXT DEFAULT 'pending',
            admin_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        for aid in ADMIN_IDS:
            c.execute('INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 999999)', (aid,))
        self.conn.commit()

    def get_user(self, uid): return self._row(self.conn.execute('SELECT * FROM users WHERE user_id=?', (uid,)).fetchone())
    def create_user(self, uid, username=None, full_name=None):
        bal = 999999 if uid in ADMIN_IDS else 0
        self.conn.execute('INSERT OR IGNORE INTO users (user_id, username, full_name, balance) VALUES (?,?,?,?)', (uid, username, full_name, bal))
        self.conn.commit()
    def get_balance(self, uid):
        r = self.conn.execute('SELECT balance FROM users WHERE user_id=?', (uid,)).fetchone()
        return r[0] if r else 0
    def update_balance(self, uid, amount):
        cur = self.get_balance(uid); new = cur + amount
        if new < 0: return False
        c = self.conn.cursor()
        c.execute('UPDATE users SET balance=? WHERE user_id=?', (new, uid))
        if amount > 0: c.execute('UPDATE users SET total_deposited=total_deposited+? WHERE user_id=?', (amount, uid))
        else: c.execute('UPDATE users SET total_spent=total_spent+? WHERE user_id=?', (abs(amount), uid))
        self.conn.commit(); return True
    def add_ai_chat(self, uid):
        self.conn.execute('UPDATE users SET ai_chats=ai_chats+1 WHERE user_id=?', (uid,))
        self.conn.commit()
    def create_request(self, uid, amount, pkg=""):
        c = self.conn.cursor()
        c.execute('INSERT INTO deposit_requests (user_id, amount, package_name) VALUES (?,?,?)', (uid, amount, pkg))
        self.conn.commit(); return c.lastrowid
    def get_pending(self):
        return [self._row(r) for r in self.conn.execute('SELECT * FROM deposit_requests WHERE status="pending" ORDER BY created_at DESC').fetchall()]
    def approve(self, rid, aid):
        r = self.conn.execute('SELECT * FROM deposit_requests WHERE id=? AND status="pending"', (rid,)).fetchone()
        if not r: return False, None
        d = self._row(r)
        self.conn.execute('UPDATE deposit_requests SET status="approved", admin_id=? WHERE id=?', (aid, rid))
        self.update_balance(d['user_id'], d['amount']); self.conn.commit(); return True, d
    def reject(self, rid, aid):
        self.conn.execute('UPDATE deposit_requests SET status="rejected", admin_id=? WHERE id=?', (aid, rid))
        self.conn.commit()
    def get_stats(self):
        c = self.conn.cursor()
        return {'users': c.execute('SELECT COUNT(*) FROM users').fetchone()[0],
                'pending': c.execute('SELECT COUNT(*) FROM deposit_requests WHERE status="pending"').fetchone()[0]}

db = Database()

# ===== AI CHAT (GPT4Free - Miễn phí) =====
def ai_chat(message, lang='vi'):
    """Chat với AI miễn phí qua g4f"""
    try:
        import g4f
        
        if lang == 'vi':
            system_prompt = "Bạn là trợ lý AI hữu ích. Trả lời ngắn gọn, thân thiện bằng tiếng Việt."
        else:
            system_prompt = "You are a helpful AI assistant. Answer briefly and friendly."
        
        response = g4f.ChatCompletion.create(
            model=g4f.models.default,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message}
            ],
            timeout=30
        )
        
        if response:
            return response[:1500]  # Giới hạn độ dài
        return "Xin lỗi, tôi không thể trả lời lúc này."
    except ImportError:
        return "Tính năng AI đang được cài đặt..."
    except Exception as e:
        return f"AI đang bận, thử lại sau nhé! ({str(e)[:50]})"

# ===== TẠO ẢNH KẾT QUẢ =====
def create_result_image(phone, country, carrier, line_type, provider, price, balance):
    """Tạo ảnh kết quả check SĐT"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        
        # Tạo ảnh 600x400
        img = Image.new('RGB', (600, 400), '#1a1a2e')
        draw = ImageDraw.Draw(img)
        
        # Tìm font (fallback mặc định nếu không có)
        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
            font_body = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        except:
            font_title = ImageFont.load_default()
            font_body = ImageFont.load_default()
        
        y = 20
        
        # Tiêu đề
        draw.text((30, y), "✅ KẾT QUẢ KIỂM TRA SĐT", fill='#00ff88', font=font_title)
        y += 40
        
        # Đường kẻ
        draw.line([(30, y), (570, y)], fill='#333', width=1)
        y += 20
        
        # Thông tin
        info = [
            f"📞 SĐT: {phone}",
            f"🌍 Quốc gia: {country}",
            f"📡 Nhà mạng: {carrier}",
            f"📋 Loại: {line_type}",
            f"🔌 API: {provider}",
            f"💵 Phí: {price:,}đ",
            f"💰 Số dư: {balance:,}đ"
        ]
        
        for line in info:
            draw.text((30, y), line, fill='#ffffff', font=font_body)
            y += 30
        
        # Footer
        y += 10
        draw.line([(30, y), (570, y)], fill='#333', width=1)
        y += 15
        draw.text((30, y), f"🤖 @checksdt100_bot | {datetime.now().strftime('%H:%M %d/%m/%Y')}", fill='#888', font=font_body)
        
        # Lưu vào BytesIO
        bio = BytesIO()
        img.save(bio, 'PNG')
        bio.seek(0)
        return bio
    except ImportError:
        return None
    except Exception as e:
        logger.error(f"Image error: {e}")
        return None

# ===== HELPER =====
def get_msg(update: Update):
    if update.callback_query and update.callback_query.message:
        return update.callback_query.message
    return update.message

# ===== PHONE CHECK =====
def check_phone(phone):
    phone = re.sub(r'[\s\-\(\)]', '', phone.strip())
    if phone.startswith('0'): phone = '+84' + phone[1:]
    elif not phone.startswith('+'): phone = '+84' + phone
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
    u = update.effective_user; msg = get_msg(update)
    if not msg: return
    db.create_user(u.id, u.username, u.full_name)
    bal = db.get_balance(u.id)
    is_ad = u.id in ADMIN_IDS
    
    text = (
        f"👋 *Chào {u.full_name}!*\n\n"
        f"📱 *Bot Check SĐT Pro*\n"
        f"🔄 3 API | 🤖 AI Chat | 🖼️ Ảnh\n\n"
        f"💰 Số dư: *{bal:,}đ*\n"
        f"💵 {PRICE_PER_CHECK:,}đ/lần check\n\n"
        "📝 *Lệnh hỗ trợ tiếng Việt:*\n"
        "• Gửi SĐT hoặc /phone <số>\n"
        "• /nap hoặc /nạp - Nạp tiền\n"
        "• /balance hoặc /số_dư\n"
        "• /ai <câu_hỏi> - Chat với AI\n"
        "• /help hoặc /giúp_đỡ"
    )
    
    kb = [
        [InlineKeyboardButton("💵 Nạp tiền", callback_data="menu_nap"),
         InlineKeyboardButton("💰 Số dư", callback_data="menu_balance")],
        [InlineKeyboardButton("🤖 Chat AI", callback_data="menu_ai"),
         InlineKeyboardButton("📋 Bảng giá", callback_data="menu_prices")],
        [InlineKeyboardButton("📖 Hướng dẫn", callback_data="menu_help")]
    ]
    if is_ad:
        kb.append([InlineKeyboardButton("👑 Admin", callback_data="admin_panel")])
    
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

# ===== CHECK HANDLER =====
async def phone_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = get_msg(update)
    if not msg: return
    if not context.args:
        await msg.reply_text("📝 `/phone <số>` hoặc gửi trực tiếp SĐT\nVD: `0912345678`", parse_mode=ParseMode.MARKDOWN)
        return
    update.message.text = ' '.join(context.args)
    await check_handler(update, context)

async def check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; msg = update.message
    if not msg: return
    
    phone = msg.text.strip()
    cleaned = re.sub(r'[\s\-\(\)]', '', phone)
    if not re.match(r'^\+?\d{7,15}$', cleaned):
        await msg.reply_text("❌ SĐT không hợp lệ!\nVD: 0912345678 hoặc +84912345678")
        return
    
    bal = db.get_balance(uid)
    if bal < PRICE_PER_CHECK:
        await msg.reply_text(
            f"❌ *Không đủ số dư!*\n💰 {bal:,}đ / 💵 {PRICE_PER_CHECK:,}đ\n\n"
            "Dùng /nap hoặc /nạp để nạp thêm",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    p = await msg.reply_text("🔄 *Đang kiểm tra...* ⚡", parse_mode=ParseMode.MARKDOWN)
    result = check_phone(phone)
    db.update_balance(uid, -PRICE_PER_CHECK)
    
    if result.get('success'):
        text = (
            f"✅ *KẾT QUẢ KIỂM TRA*\n"
            f"{'─' * 25}\n"
            f"📞 `{result['phone']}`\n"
            f"🌍 *{result['country']}*\n"
            f"📡 *{result['carrier']}*\n"
            f"📋 {result['line_type']}\n"
            f"💵 {PRICE_PER_CHECK:,}đ | 💰 {db.get_balance(uid):,}đ"
        )
        
        # Tạo ảnh kết quả
        img = create_result_image(
            result['phone'], result['country'], result['carrier'],
            result['line_type'], result.get('provider', 'Numverify'),
            PRICE_PER_CHECK, db.get_balance(uid)
        )
        
        if img:
            await p.delete()
            await msg.reply_photo(
                photo=img,
                caption=text,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await p.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    else:
        db.update_balance(uid, PRICE_PER_CHECK)
        text = f"❌ *Thất bại!*\n📞 `{phone}`\n💵 Đã hoàn {PRICE_PER_CHECK:,}đ"
        await p.edit_text(text, parse_mode=ParseMode.MARKDOWN)

# ===== AI HANDLER =====
async def ai_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; msg = get_msg(update)
    if not msg: return
    
    if not context.args:
        await msg.reply_text(
            "🤖 *AI Chat*\n\n"
            "Dùng: `/ai <câu hỏi>`\n"
            "VD: `/ai thời tiết hôm nay thế nào?`\n"
            "    `/ai giải thích về số điện thoại`\n\n"
            "💡 AI miễn phí, không giới hạn!",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    question = ' '.join(context.args)
    p = await msg.reply_text("🤖 *AI đang suy nghĩ...*", parse_mode=ParseMode.MARKDOWN)
    
    # Gọi AI
    answer = ai_chat(question, 'vi')
    db.add_ai_chat(uid)
    
    text = (
        f"🤖 *AI TRẢ LỜI*\n"
        f"{'─' * 25}\n"
        f"❓ *Hỏi:* {question[:200]}\n\n"
        f"💬 *Đáp:* {answer}\n\n"
        f"💡 AI miễn phí | /ai để hỏi tiếp"
    )
    
    if len(text) > 4000:
        text = text[:3900] + "...\n\n(Xem tiếp: /ai <câu_hỏi>)"
    
    await p.edit_text(text, parse_mode=ParseMode.MARKDOWN)

# ===== BALANCE HANDLER =====
async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; msg = get_msg(update)
    if not msg: return
    u = db.get_user(uid)
    bal = db.get_balance(uid)
    
    text = (
        f"💰 *TÀI KHOẢN CỦA BẠN*\n"
        f"{'─' * 25}\n"
        f"💵 Số dư: *{bal:,}đ*\n"
        f"📊 Đã check: *{u['total_checks']}* lần\n"
        f"💸 Đã chi: *{u['total_spent']:,}đ*\n"
        f"💳 Đã nạp: *{u['total_deposited']:,}đ*\n"
        f"🤖 AI chats: *{u.get('ai_chats', 0)}* lần\n\n"
        f"🎯 Có thể check thêm: *{int(bal/PRICE_PER_CHECK)}* lần"
    )
    
    kb = [[InlineKeyboardButton("💵 Nạp thêm", callback_data="menu_nap"),
           InlineKeyboardButton("🤖 Hỏi AI", callback_data="menu_ai")]]
    
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

# ===== NAP HANDLER =====
async def nap_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; msg = get_msg(update)
    if not msg: return
    
    text = (
        "💵 *NẠP TIỀN VÀO TÀI KHOẢN*\n"
        f"{'─' * 25}\n\n"
        f"🏦 *Ngân hàng:* {BANK_INFO['bank']}\n"
        f"💳 *Số TK:* `{BANK_INFO['account']}`\n"
        f"👤 *Chủ TK:* {BANK_INFO['name']}\n"
        f"📩 *Nội dung CK:* `NAP {uid}`\n\n"
        "📌 *Chọn gói bên dưới hoặc nhập số tiền:*\n"
        "/naptien <số_tiền> - Gửi yêu cầu nạp\n\n"
        "💡 Sau khi CK, admin sẽ duyệt trong 1-5 phút"
    )
    
    kb = []
    row = []
    for pkg in PACKAGES:
        row.append(InlineKeyboardButton(
            f"{pkg['emoji']} {pkg['name']}\n{pkg['amount']:,}đ",
            callback_data=f"nap_{pkg['amount']}"
        ))
        if len(row) == 2:
            kb.append(row); row = []
    if row: kb.append(row)
    
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

# ===== NAPTIEN HANDLER =====
async def naptien_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; msg = get_msg(update)
    if not msg: return
    
    if not context.args:
        await msg.reply_text(
            "📝 *GỬI YÊU CẦU NẠP TIỀN*\n\n"
            "`/naptien <số_tiền>`\n"
            "VD: `/naptien 50000`\n\n"
            "⚠️ Nhớ chuyển khoản trước khi gửi!\n"
            f"📩 ND: `NAP {uid}`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        amount = int(context.args[0])
        if amount < 10000:
            await msg.reply_text("❌ Tối thiểu 10,000đ!")
            return
        
        rid = db.create_request(uid, amount)
        await msg.reply_text(
            f"✅ *Yêu cầu #{rid} đã gửi!*\n\n"
            f"💵 Số tiền: *{amount:,}đ*\n"
            f"⏳ Trạng thái: *Chờ duyệt*\n\n"
            f"🏦 Vui lòng CK:\n"
            f"• {BANK_INFO['bank']}\n"
            f"• `{BANK_INFO['account']}`\n"
            f"• {BANK_INFO['name']}\n"
            f"• ND: `NAP {uid}`\n\n"
            "⏰ Admin sẽ duyệt trong 1-5 phút",
            parse_mode=ParseMode.MARKDOWN
        )
        
        for aid in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    aid,
                    f"🔔 *Yêu cầu mới #{rid}*\n👤 `{uid}`\n💵 *{amount:,}đ*\n/duyet {rid}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except: pass
    except:
        await msg.reply_text("❌ Số không hợp lệ!")

# ===== HELP HANDLER =====
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = get_msg(update)
    if not msg: return
    
    text = (
        "📖 *HƯỚNG DẪN SỬ DỤNG*\n"
        f"{'─' * 25}\n\n"
        "📱 *Kiểm tra SĐT:*\n"
        "• Gửi trực tiếp: `0912345678`\n"
        "• Lệnh: `/phone 0912345678`\n\n"
        "🤖 *AI Chat:*\n"
        "• `/ai <câu hỏi>` - Hỏi AI miễn phí\n\n"
        "💵 *Nạp tiền:*\n"
        "• `/nap` hoặc `/nạp` - Xem hướng dẫn\n"
        "• `/naptien <tiền>` - Gửi yêu cầu\n\n"
        "💰 *Tài khoản:*\n"
        "• `/balance` hoặc `/số_dư`\n\n"
        "📋 *Lệnh Admin:*\n"
        "/duyet <id> | /huy <id> | /pending"
    )
    
    kb = [
        [InlineKeyboardButton("🤖 Chat AI", callback_data="menu_ai"),
         InlineKeyboardButton("💵 Nạp tiền", callback_data="menu_nap")]
    ]
    
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

# ===== ADMIN HANDLERS =====
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    msg = get_msg(update)
    if not msg: return
    s = db.get_stats()
    await msg.reply_text(
        f"👑 *ADMIN PANEL*\n👥 Users: {s['users']}\n⏳ Pending: {s['pending']}\n\n"
        "/pending | /duyet <id> | /huy <id>",
        parse_mode=ParseMode.MARKDOWN
    )

async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    msg = get_msg(update)
    if not msg: return
    pending = db.get_pending()
    if not pending:
        await msg.reply_text("✅ Không có yêu cầu chờ!")
        return
    text = f"📋 *YÊU CẦU CHỜ* ({len(pending)})\n\n"
    for r in pending:
        text += f"🆔 #{r['id']} | 👤 `{r['user_id']}` | 💵 {r['amount']:,}đ\n/duyet {r['id']} | /huy {r['id']}\n\n"
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def duyet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    msg = get_msg(update)
    if not msg or not context.args: return
    try:
        rid = int(context.args[0])
        ok, data = db.approve(rid, update.effective_user.id)
        if ok:
            await msg.reply_text(f"✅ Duyệt #{rid} - {data['amount']:,}đ")
            try:
                await context.bot.send_message(data['user_id'],
                    f"✅ *Nạp tiền thành công!*\n💵 +{data['amount']:,}đ\n💰 Dư: {db.get_balance(data['user_id']):,}đ",
                    parse_mode=ParseMode.MARKDOWN)
            except: pass
        else:
            await msg.reply_text("❌ Không tìm thấy!")
    except: pass

async def huy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    msg = get_msg(update)
    if not msg or not context.args: return
    try:
        rid = int(context.args[0])
        db.reject(rid, update.effective_user.id)
        await msg.reply_text(f"🚫 Hủy #{rid}")
    except: pass

# ===== CALLBACK =====
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data; uid = update.effective_user.id; msg = q.message
    if not msg: return
    
    if d == "menu_nap":
        await nap_cmd(update, context)
    elif d == "menu_balance":
        await balance_cmd(update, context)
    elif d == "menu_ai":
        await msg.reply_text(
            "🤖 *AI Chat*\nDùng: `/ai <câu hỏi>`\n"
            "VD: `/ai hôm nay thế nào?`\n\n"
            "💡 AI miễn phí, không giới hạn!",
            parse_mode=ParseMode.MARKDOWN
        )
    elif d == "menu_prices":
        text = "📋 *BẢNG GIÁ*\n\n"
        for pkg in PACKAGES:
            text += f"{pkg['emoji']} {pkg['name']}: {pkg['amount']:,}đ = {pkg['checks']} lần\n"
        text += f"\n💵 1 lần check: {PRICE_PER_CHECK:,}đ\n🤖 AI Chat: *Miễn phí*"
        await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    elif d == "menu_help":
        await help_cmd(update, context)
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
                await context.bot.send_message(aid,
                    f"🔔 Yêu cầu #{rid}\n👤 `{uid}`\n💵 {amount:,}đ\n/duyet {rid}",
                    parse_mode=ParseMode.MARKDOWN)
            except: pass

# ===== MAIN =====
if __name__ == '__main__':
    print("=" * 50)
    print("🤖 BOT CHECK SĐT PRO - RAILWAY")
    print(f"👑 Admin: {ADMIN_IDS}")
    print(f"🐍 Python: {sys.version}")
    print(f"🆕 AI Chat | 🖼️ Ảnh | 🇻🇳 Tiếng Việt")
    print("=" * 50)
    
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Commands - Hỗ trợ cả tiếng Việt
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("phone", phone_cmd))
    app.add_handler(CommandHandler("check", phone_cmd))
    app.add_handler(CommandHandler(["nap", "nạp"], nap_cmd))
    app.add_handler(CommandHandler("naptien", naptien_cmd))
    app.add_handler(CommandHandler(["balance", "số_dư", "sodu"], balance_cmd))
    app.add_handler(CommandHandler("ai", ai_cmd))
    app.add_handler(CommandHandler(["help", "giúp_đỡ", "giupdo"], help_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("pending", pending_cmd))
    app.add_handler(CommandHandler("duyet", duyet_cmd))
    app.add_handler(CommandHandler("huy", huy_cmd))
    
    # Callback + Message
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_handler))
    
    print("✅ Bot ready!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
