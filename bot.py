#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
💎 PREMIUM OTP BOT — SQLite edition
• ENV নেই: সব সেটিংস SQLite ডাটাবেসে (premium_bot.db) থাকে
• Admin Panel (/admin) থেকে পুরো বট কন্ট্রোল: API, রেট, লিংক, গ্রুপ, সার্ভিস,
  পেমেন্ট মেথড, ফোর্স জয়েন, অ্যাডমিন, ব্রডকাস্ট, উইথড্র, ইউজার, ব্যাকআপ
• একসাথে ৩টা নম্বর + Copy বাটন + Change Number / Change Country / Change Service / OTP Group
Requires: pip install -U python-telegram-bot httpx   (PTB 21.7+ for copy buttons)
"""
import asyncio
import csv
import html
import io
import os
import random
import re
import sqlite3
import string
import threading
from datetime import datetime, timedelta

import httpx
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                      ReplyKeyboardMarkup, Update)
from telegram.error import BadRequest, RetryAfter
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

try:
    from telegram import CopyTextButton
except ImportError:  # পুরোনো PTB
    CopyTextButton = None

# =====================================================================
# ⚙️ BASIC CONFIG  (শুধু এই ৩টা ফাইলে বসাতে হবে — বাকি সব Admin Panel থেকে)
# =====================================================================
BOT_TOKEN = "9164268222:AAFVB08ftmp3akBs-Im6xBJtvqQwiHzahqs"      # @BotFather থেকে নতুন টোকেন
OWNER_IDS = [6759409823]                         # আপনার Telegram numeric ID (Owner)
DB_PATH = "premium_bot.db"

# প্রথমবার ডাটাবেস বানানোর সময় এই ডিফল্টগুলো সেভ হবে; পরে Admin Panel → Settings থেকে বদলান
DEFAULT_API_BASE = "https://api.2oo9.cloud/MXS47FLFX0U/tnevs/@public"
DEFAULT_API_KEY = "MFI570T9MLY"
DEFAULT_OTP_GROUP_ID = "-1003804688570"
SEED_SERVICES = ["FACEBOOK", "WHATSAPP", "TELEGRAM", "INSTAGRAM"]
SEED_METHODS = ["BKASH", "NAGAD", "ROCKET", "BINANCE"]

# =====================================================================
# 🗄 DATABASE
# =====================================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users(
  uid INTEGER PRIMARY KEY, name TEXT DEFAULT '', username TEXT DEFAULT '', lang TEXT,
  balance REAL DEFAULT 0, total_earned REAL DEFAULT 0, banned INTEGER DEFAULT 0,
  joined TEXT, referred_by INTEGER, ref_count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS admins(uid INTEGER PRIMARY KEY, added TEXT);
CREATE TABLE IF NOT EXISTS services(sid TEXT PRIMARY KEY, enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS methods(name TEXT PRIMARY KEY, enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT, chat TEXT, link TEXT);
CREATE TABLE IF NOT EXISTS numbers(
  id INTEGER PRIMARY KEY AUTOINCREMENT, batch TEXT, uid INTEGER, number TEXT, rng TEXT, sid TEXT,
  chat_id INTEGER, msg_id INTEGER, created TEXT, otp TEXT, sms TEXT, otp_at TEXT);
CREATE INDEX IF NOT EXISTS ix_num_number ON numbers(number);
CREATE INDEX IF NOT EXISTS ix_num_batch ON numbers(batch);
CREATE INDEX IF NOT EXISTS ix_num_uid ON numbers(uid); 
CREATE TABLE IF NOT EXISTS otps(key TEXT PRIMARY KEY, uid INTEGER, number TEXT, otp TEXT, reward REAL, ts TEXT);
CREATE TABLE IF NOT EXISTS withdraws(
  pid TEXT PRIMARY KEY, uid INTEGER, method TEXT, amount REAL, account TEXT, status TEXT,
  created TEXT, processed_at TEXT, processed_by INTEGER);
CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, uid INTEGER, action TEXT, details TEXT);
"""

_db = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
_db.row_factory = sqlite3.Row
_lock = threading.RLock()
with _lock:
    _db.execute("PRAGMA journal_mode=WAL")
    _db.executescript(SCHEMA)


def xc(sql, p=()):
    """execute → rowcount"""
    with _lock:
        return _db.execute(sql, p).rowcount


def one(sql, p=()):
    with _lock:
        r = _db.execute(sql, p).fetchone()
        return dict(r) if r else None


def many(sql, p=()):
    with _lock:
        return [dict(r) for r in _db.execute(sql, p).fetchall()]


def scalar(sql, p=()):
    with _lock:
        r = _db.execute(sql, p).fetchone()
        return r[0] if r else None


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def today0():
    n = datetime.now()
    return datetime(n.year, n.month, n.day).isoformat(timespec="seconds")


def log(uid, action, details=""):
    xc("INSERT INTO logs(ts,uid,action,details) VALUES(?,?,?,?)", (now_iso(), uid, action, str(details)[:300]))


# =====================================================================
# 🎛 SETTINGS (DB-backed, Admin Panel থেকে এডিটযোগ্য)
# =====================================================================
# key: (default, label, type, group)
SETTINGS = {
    "bot_name": ("PREMIUM OTP", "🏷 Bot name", "text", "brand"),
    "welcome_en": ("", "📝 Welcome text (EN)", "text", "brand"),
    "welcome_bn": ("", "📝 Welcome text (BN)", "text", "brand"),
    "support_link": ("", "💬 Support link", "text", "brand"),
    "otp_rate": (0.20, "💰 OTP reward (BDT)", "float", "economy"),
    "ref_bonus": (0.0, "🎁 Referral bonus (BDT)", "float", "economy"),
    "min_wd": (50.0, "📉 Min withdraw (BDT)", "float", "economy"),
    "max_wd": (10000.0, "📈 Max withdraw (BDT)", "float", "economy"),
    "numbers_per_req": (3, "🔢 Numbers per request (1-6)", "int", "numbers"),
    "number_ttl": (60, "⏱ Number validity (minutes)", "int", "numbers"),
    "getnum_on": (True, "📲 Get Number", "bool", "numbers"),
    "custom_range_on": (True, "⚙️ Custom range", "bool", "numbers"),
    "otp_group_id": (DEFAULT_OTP_GROUP_ID, "📣 OTP group chat ID", "text", "links"),
    "otp_group_link": ("", "🔗 OTP group link (button)", "text", "links"),
    "channel_link": ("", "📢 Channel link (button)", "text", "links"),
    "panel_link": ("", "🤖 Panel/Bot link (button)", "text", "links"),
    "api_base": (DEFAULT_API_BASE, "🌐 API base URL", "text", "api"),
    "api_key": (DEFAULT_API_KEY, "🔑 API key", "text", "api"),
    "check_interval": (2.0, "⏲ OTP check interval (sec)", "float", "api"),
    "live_interval": (25, "⏲ Service refresh (sec)", "int", "api"),
    "maintenance": (False, "🛠 Maintenance mode", "bool", "system"),
    "withdraw_on": (True, "💸 Withdraw enabled", "bool", "system"),
    "force_join_on": (False, "📌 Force join", "bool", "system"),
    "group_post_on": (True, "📣 Post OTP to group", "bool", "system"),
    "mask_group": (True, "🙈 Mask numbers in group", "bool", "system"),
}
GROUPS = [("brand", "🎨 Branding & Texts"), ("economy", "💰 Economy"), ("numbers", "📲 Numbers"),
          ("links", "🔗 Links & Group"), ("api", "🔌 API"), ("system", "🛠 System")]
_cache = {}


def _conv(key, raw):
    d, _, t, _ = SETTINGS[key]
    try:
        if t == "bool":
            return str(raw).lower() in ("1", "true", "yes", "on")
        if t == "int":
            return int(float(raw))
        if t == "float":
            return float(raw)
        return str(raw)
    except Exception:
        return d


def load_settings():
    rows = {r["key"]: r["value"] for r in many("SELECT key,value FROM settings")}
    for k, (d, *_rest) in SETTINGS.items():
        _cache[k] = _conv(k, rows[k]) if k in rows else d


def S(key):
    return _cache.get(key, SETTINGS[key][0])


def set_S(key, val):
    raw = ("1" if val else "0") if SETTINGS[key][2] == "bool" else str(val)
    xc("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, raw))
    _cache[key] = _conv(key, raw)


load_settings()
for _s in SEED_SERVICES:
    xc("INSERT OR IGNORE INTO services(sid,enabled) VALUES(?,1)", (_s,))
for _m in SEED_METHODS:
    xc("INSERT OR IGNORE INTO methods(name,enabled) VALUES(?,1)", (_m,))
for _o in OWNER_IDS:
    xc("INSERT OR IGNORE INTO admins(uid,added) VALUES(?,?)", (_o, now_iso()))


def is_owner(uid):
    return int(uid) in OWNER_IDS


def is_admin(uid):
    return is_owner(uid) or scalar("SELECT 1 FROM admins WHERE uid=?", (int(uid),)) == 1


def admin_ids():
    return {r["uid"] for r in many("SELECT uid FROM admins")} | set(OWNER_IDS)


# =====================================================================
# 👤 USERS
# =====================================================================
def user_get(uid, tg=None):
    uid = int(uid)
    xc("INSERT OR IGNORE INTO users(uid,joined) VALUES(?,?)", (uid, now_iso()))
    if tg is not None:
        xc("UPDATE users SET name=?, username=? WHERE uid=?", (tg.full_name or "", tg.username or "", uid))
    return one("SELECT * FROM users WHERE uid=?", (uid,))


def ulang(uid):
    r = one("SELECT lang FROM users WHERE uid=?", (int(uid),))
    return (r or {}).get("lang") or "bn"


def add_balance(uid, amount):
    if amount >= 0:
        xc("UPDATE users SET balance=ROUND(balance+?,2), total_earned=ROUND(total_earned+?,2) WHERE uid=?",
           (amount, amount, int(uid)))
    else:
        xc("UPDATE users SET balance=MAX(0,ROUND(balance+?,2)) WHERE uid=?", (amount, int(uid)))
    return scalar("SELECT balance FROM users WHERE uid=?", (int(uid),)) or 0.0


def user_stats(uid):
    t0 = today0()
    return {
        "n_today": scalar("SELECT COUNT(*) FROM numbers WHERE uid=? AND created>=?", (uid, t0)),
        "n_all": scalar("SELECT COUNT(*) FROM numbers WHERE uid=?", (uid,)),
        "o_today": scalar("SELECT COUNT(*) FROM otps WHERE uid=? AND ts>=?", (uid, t0)),
        "o_all": scalar("SELECT COUNT(*) FROM otps WHERE uid=?", (uid,)),
    }


# =====================================================================
# 🌍 HELPERS
# =====================================================================
COUNTRY_MAP = {
    "237": ("🇨🇲", "Cameroon"), "225": ("🇨🇮", "Ivory Coast"), "261": ("🇲🇬", "Madagascar"), "20": ("🇪🇬", "Egypt"),
    "27": ("🇿🇦", "South Africa"), "234": ("🇳🇬", "Nigeria"), "254": ("🇰🇪", "Kenya"), "233": ("🇬🇭", "Ghana"),
    "212": ("🇲🇦", "Morocco"), "213": ("🇩🇿", "Algeria"), "216": ("🇹🇳", "Tunisia"), "218": ("🇱🇾", "Libya"),
    "249": ("🇸🇩", "Sudan"), "251": ("🇪🇹", "Ethiopia"), "252": ("🇸🇴", "Somalia"), "253": ("🇩🇯", "Djibouti"),
    "255": ("🇹🇿", "Tanzania"), "256": ("🇺🇬", "Uganda"), "257": ("🇧🇮", "Burundi"), "258": ("🇲🇿", "Mozambique"),
    "260": ("🇿🇲", "Zambia"), "263": ("🇿🇼", "Zimbabwe"), "264": ("🇳🇦", "Namibia"), "265": ("🇲🇼", "Malawi"),
    "266": ("🇱🇸", "Lesotho"), "267": ("🇧🇼", "Botswana"), "268": ("🇸🇿", "Eswatini"), "269": ("🇰🇲", "Comoros"),
    "220": ("🇬🇲", "Gambia"), "221": ("🇸🇳", "Senegal"), "222": ("🇲🇷", "Mauritania"), "223": ("🇲🇱", "Mali"),
    "224": ("🇬🇳", "Guinea"), "226": ("🇧🇫", "Burkina Faso"), "227": ("🇳🇪", "Niger"), "228": ("🇹🇬", "Togo"),
    "229": ("🇧🇯", "Benin"), "230": ("🇲🇺", "Mauritius"), "231": ("🇱🇷", "Liberia"), "232": ("🇸🇱", "Sierra Leone"),
    "235": ("🇹🇩", "Chad"), "236": ("🇨🇫", "Central African Rep."), "238": ("🇨🇻", "Cape Verde"),
    "239": ("🇸🇹", "Sao Tome"), "240": ("🇬🇶", "Equatorial Guinea"), "241": ("🇬🇦", "Gabon"),
    "242": ("🇨🇬", "Congo"), "243": ("🇨🇩", "DR Congo"), "244": ("🇦🇴", "Angola"), "245": ("🇬🇼", "Guinea-Bissau"),
    "248": ("🇸🇨", "Seychelles"), "250": ("🇷🇼", "Rwanda"), "291": ("🇪🇷", "Eritrea"), "40": ("🇷🇴", "Romania"),
    "44": ("🇬🇧", "United Kingdom"), "33": ("🇫🇷", "France"), "49": ("🇩🇪", "Germany"), "39": ("🇮🇹", "Italy"),
    "34": ("🇪🇸", "Spain"), "31": ("🇳🇱", "Netherlands"), "32": ("🇧🇪", "Belgium"), "41": ("🇨🇭", "Switzerland"),
    "43": ("🇦🇹", "Austria"), "46": ("🇸🇪", "Sweden"), "47": ("🇳🇴", "Norway"), "45": ("🇩🇰", "Denmark"),
    "358": ("🇫🇮", "Finland"), "351": ("🇵🇹", "Portugal"), "353": ("🇮🇪", "Ireland"), "36": ("🇭🇺", "Hungary"),
    "48": ("🇵🇱", "Poland"), "380": ("🇺🇦", "Ukraine"), "370": ("🇱🇹", "Lithuania"), "371": ("🇱🇻", "Latvia"),
    "372": ("🇪🇪", "Estonia"), "373": ("🇲🇩", "Moldova"), "374": ("🇦🇲", "Armenia"), "375": ("🇧🇾", "Belarus"),
    "381": ("🇷🇸", "Serbia"), "382": ("🇲🇪", "Montenegro"), "385": ("🇭🇷", "Croatia"), "386": ("🇸🇮", "Slovenia"),
    "387": ("🇧🇦", "Bosnia"), "389": ("🇲🇰", "North Macedonia"), "352": ("🇱🇺", "Luxembourg"),
    "354": ("🇮🇸", "Iceland"), "355": ("🇦🇱", "Albania"), "356": ("🇲🇹", "Malta"), "357": ("🇨🇾", "Cyprus"),
    "359": ("🇧🇬", "Bulgaria"), "421": ("🇸🇰", "Slovakia"), "420": ("🇨🇿", "Czechia"), "1": ("🇺🇸", "United States"),
    "7": ("🇷🇺", "Russia"), "91": ("🇮🇳", "India"), "92": ("🇵🇰", "Pakistan"), "880": ("🇧🇩", "Bangladesh"),
    "86": ("🇨🇳", "China"), "81": ("🇯🇵", "Japan"), "82": ("🇰🇷", "South Korea"), "84": ("🇻🇳", "Vietnam"),
    "66": ("🇹🇭", "Thailand"), "62": ("🇮🇩", "Indonesia"), "60": ("🇲🇾", "Malaysia"), "65": ("🇸🇬", "Singapore"),
    "63": ("🇵🇭", "Philippines"), "95": ("🇲🇲", "Myanmar"), "94": ("🇱🇰", "Sri Lanka"), "977": ("🇳🇵", "Nepal"),
    "93": ("🇦🇫", "Afghanistan"), "98": ("🇮🇷", "Iran"), "90": ("🇹🇷", "Turkey"), "964": ("🇮🇶", "Iraq"),
    "963": ("🇸🇾", "Syria"), "961": ("🇱🇧", "Lebanon"), "962": ("🇯🇴", "Jordan"), "965": ("🇰🇼", "Kuwait"),
    "966": ("🇸🇦", "Saudi Arabia"), "967": ("🇾🇪", "Yemen"), "968": ("🇴🇲", "Oman"), "971": ("🇦🇪", "UAE"),
    "972": ("🇮🇱", "Israel"), "973": ("🇧🇭", "Bahrain"), "974": ("🇶🇦", "Qatar"), "994": ("🇦🇿", "Azerbaijan"),
    "995": ("🇬🇪", "Georgia"), "996": ("🇰🇬", "Kyrgyzstan"), "992": ("🇹🇯", "Tajikistan"),
    "993": ("🇹🇲", "Turkmenistan"), "998": ("🇺🇿", "Uzbekistan"), "855": ("🇰🇭", "Cambodia"), "856": ("🇱🇦", "Laos"),
    "976": ("🇲🇳", "Mongolia"), "55": ("🇧🇷", "Brazil"), "52": ("🇲🇽", "Mexico"), "54": ("🇦🇷", "Argentina"),
    "57": ("🇨🇴", "Colombia"), "51": ("🇵🇪", "Peru"), "58": ("🇻🇪", "Venezuela"), "56": ("🇨🇱", "Chile"),
    "593": ("🇪🇨", "Ecuador"), "591": ("🇧🇴", "Bolivia"), "595": ("🇵🇾", "Paraguay"), "598": ("🇺🇾", "Uruguay"),
    "502": ("🇬🇹", "Guatemala"), "503": ("🇸🇻", "El Salvador"), "504": ("🇭🇳", "Honduras"),
    "506": ("🇨🇷", "Costa Rica"), "507": ("🇵🇦", "Panama"), "509": ("🇭🇹", "Haiti"), "501": ("🇧🇿", "Belize"),
    "61": ("🇦🇺", "Australia"), "64": ("🇳🇿", "New Zealand"), "675": ("🇵🇬", "Papua New Guinea"),
    "679": ("🇫🇯", "Fiji"), "1246": ("🇧🇧", "Barbados"), "1876": ("🇯🇲", "Jamaica"), "53": ("🇨🇺", "Cuba"),
    "592": ("🇬🇾", "Guyana"),
}
_PREFIXES = sorted(COUNTRY_MAP, key=len, reverse=True)
SERVICE_LOGO = {"FACEBOOK": "🔵 FACEBOOK", "INSTAGRAM": "📸 INSTAGRAM", "WHATSAPP": "🟢 WHATSAPP", "TELEGRAM": "✈️ TELEGRAM"}
LINE = "━━━━━━━━━━━━━━━━━━━━"
NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]


def esc(x):
    return html.escape(str(x))


def digits(x):
    return re.sub(r"\D", "", str(x))


def country_of(number):
    d = digits(number)
    for p in _PREFIXES:
        if d.startswith(p):
            return COUNTRY_MAP[p]
    return ("🌍", "Unknown")


def logo(sid):
    return SERVICE_LOGO.get(str(sid).upper(), f"📩 {esc(str(sid).upper())}")


def detect_service(sms):
    low = (sms or "").lower()
    for kw, name in (("facebook", "FACEBOOK"), ("instagram", "INSTAGRAM"), ("whatsapp", "WHATSAPP"),
                     ("telegram", "TELEGRAM"), ("insta", "INSTAGRAM"), ("fb", "FACEBOOK")):
        if kw in low:
            return name
    return ""


def extract_otp(text):
    if not text:
        return "N/A"
    m = re.search(r"\b(\d{3}[\s-]\d{3})\b", text)
    if m:
        return re.sub(r"\D", "", m.group(1))
    m = re.search(r"\b(\d{4,8})\b", text)
    return m.group(1) if m else "N/A"


def mask(num):
    d = digits(num)
    return f"{d[:4]}****{d[-4:]}" if len(d) > 8 else d


def fm(x):
    return f"{float(x or 0):.2f}"


def head(icon, title):
    return f"{icon} <b>{esc(title)}</b>\n{LINE}\n"


# =====================================================================
# 🔘 BUTTON HELPERS (style + copy_text, নিরাপদ ফলব্যাক সহ)
# =====================================================================
def B(text, cb=None, url=None, copy=None, style=None):
    kw = {}
    if copy is not None and CopyTextButton is not None:
        kw["copy_text"] = CopyTextButton(text=str(copy)[:256])
    elif copy is not None:
        kw["callback_data"] = f"cp:{str(copy)[:50]}"
    elif url:
        kw["url"] = url
    else:
        kw["callback_data"] = cb
    if style:
        try:
            return InlineKeyboardButton(text, style=style, **kw)
        except TypeError:
            try:
                return InlineKeyboardButton(text, api_kwargs={"style": style}, **kw)
            except Exception:
                pass
    return InlineKeyboardButton(text, **kw)


def KB(text, style=None):
    if style:
        try:
            return KeyboardButton(text, style=style)
        except TypeError:
            pass
    return KeyboardButton(text)


def valid_url(u):
    return bool(u) and str(u).startswith(("http://", "https://", "tg://"))


# =====================================================================
# 🌐 I18N
# =====================================================================
MENU = {
    "get": ("📲 GET NUMBER", "📲 নম্বর নিন"),
    "bal": ("💰 BALANCE", "💰 ব্যালেন্স"),
    "ref": ("👥 REFER & EARN", "👥 রেফার করুন"),
    "prof": ("👤 PROFILE", "👤 প্রোফাইল"),
    "top": ("🏆 LEADERBOARD", "🏆 লিডারবোর্ড"),
    "lang": ("🌐 LANGUAGE", "🌐 ভাষা"),
}
LABEL2KEY = {lbl: k for k, pair in MENU.items() for lbl in pair}

T = {
    "en": {
        "welcome": "💎 <b>{bot}</b>\n" + LINE + "\n👋 Welcome, <b>{name}</b>\n\n"
                   "<blockquote>💰 Balance  <b>{bal} BDT</b>\n📲 Numbers today  <b>{n}</b>\n🔑 OTP today  <b>{o}</b></blockquote>\n"
                   "⚡ <i>Instant numbers • Live OTP • Fast payout</i>",
        "banned": "🛑 <b>You are banned.</b>",
        "maint": "🛠 <b>Under maintenance</b>\nWe'll be back shortly.",
        "off": "⛔ This feature is temporarily disabled by admin.",
        "join": "📌 <b>JOIN REQUIRED</b>\n" + LINE + "\nPlease join our channel(s) to use the bot, then tap <b>I've Joined</b>.",
        "joined_btn": "✅ I've Joined", "not_joined": "❌ You haven't joined all channels yet!",
        "pick_lang": "🌐 <b>Choose your language / ভাষা বাছাই করুন</b>",
        "lang_set": "✅ Language updated.",
        "svc_title": "📲 <b>SELECT SERVICE</b>\n" + LINE + "\nChoose the app you want a number for:",
        "svc_none": "⚠️ <b>No live services right now</b>\nPlease try again in a few seconds.",
        "country_title": "🌍 <b>SELECT COUNTRY</b>\n" + LINE + "\n🎯 Service: <b>{svc}</b>",
        "custom_btn": "⚙️ Custom Range",
        "custom_prompt": "⚙️ <b>CUSTOM RANGE</b>\n" + LINE + "\nSend a range like <code>234XXX</code>",
        "bad_range": "🛑 Invalid range! Example: <code>234XXX</code>",
        "alloc_wait": "⏳ <b>Allocating premium numbers…</b>",
        "alloc_fail": "🛑 <b>No numbers available</b>\nThis range has no free numbers right now. Try another country.",
        "card_title": "NUMBERS READY", "country": "Country", "service": "Service", "range": "Range",
        "waiting": "⏳ waiting", "reward": "Reward per OTP", "scan": "📡 <i>Listening for OTP… it appears here automatically.</i>",
        "got_otp": "✅ OTP received — new numbers anytime with Change Number.",
        "b_change_num": "🔄 Change Number", "b_change_cty": "🌍 Change Country", "b_change_svc": "🧩 Change Service",
        "b_group": "📢 OTP Group", "b_back": "◀️ Back", "b_cancel": "✖️ Cancel", "b_copy_otp": "Copy OTP",
        "b_copy_num": "Copy Number",
        "otp_title": "OTP RECEIVED", "credited": "credited to your wallet",
        "bal_title": "WALLET", "bal_body": "<blockquote>💵 Available  <b>{bal} BDT</b>\n📈 Total earned  <b>{earned} BDT</b></blockquote>",
        "b_withdraw": "💸 Withdraw",
        "wd_min": "<blockquote>💰 Balance: <b>{bal} BDT</b>\n📉 Minimum withdraw: <b>{min} BDT</b></blockquote>",
        "wd_method": "💳 <b>SELECT PAYMENT METHOD</b>",
        "wd_amount": "🪙 Enter amount (Min {min} • Max {max} BDT):",
        "wd_account_bd": "📲 Send your wallet number (e.g. <code>017XXXXXXXX</code>):",
        "wd_account_any": "📲 Send your receiving ID / address:",
        "wd_summary": "✨ <b>CONFIRM WITHDRAW</b>\n" + LINE + "\n<blockquote>💳 {method}\n📲 {acct}\n🪙 {amount} BDT</blockquote>",
        "wd_ok": "✅ <b>REQUEST SUBMITTED</b>\n" + LINE + "\n<blockquote>🆔 <code>{pid}</code>\n💳 {method} • {acct}\n🪙 {amount} BDT</blockquote>\nAdmin will review it shortly.",
        "wd_cancelled": "❌ Withdraw cancelled.", "wd_insufficient": "🚫 Insufficient balance!",
        "wd_bad_amount": "⚠️ Enter a valid amount between {min} and {max}.",
        "wd_bad_acct": "⚠️ Invalid account format!",
        "wd_paid": "✅ <b>WITHDRAW APPROVED</b>\n<blockquote>💳 {method} • {acct}\n🪙 {amount} BDT\n🆔 <code>{pid}</code></blockquote>Payment sent. Thank you!",
        "wd_rej": "❌ <b>WITHDRAW REJECTED</b>\n<blockquote>💳 {method} • {acct}\n🪙 {amount} BDT\n🆔 <code>{pid}</code></blockquote>💰 Amount refunded to your wallet.",
        "prof_title": "PROFILE",
        "prof_body": "<blockquote>🏷 Name  <b>{name}</b>\n🆔 ID  <code>{uid}</code>\n💰 Balance  <b>{bal} BDT</b>\n👥 Referrals  <b>{refs}</b></blockquote>\n"
                     "📊 <b>Today</b>  📱 {n_t} • 🔑 {o_t}\n🌐 <b>All-time</b>  📱 {n_a} • 🔑 {o_a}",
        "ref_title": "REFERRAL CENTER",
        "ref_body": "<blockquote>👥 Invites  <b>{refs}</b>\n🎁 Bonus per invite  <b>{bonus} BDT</b></blockquote>\n🔗 <b>Your link</b>\n<code>{link}</code>",
        "ref_btn": "👥 Invited users", "ref_none": "No invites yet.",
        "top_title": "TOP EARNERS", "fallback": "🔹 Choose an option from the menu below:",
        "support": "💬 Support",
    },
    "bn": {
        "welcome": "💎 <b>{bot}</b>\n" + LINE + "\n👋 স্বাগতম, <b>{name}</b>\n\n"
                   "<blockquote>💰 ব্যালেন্স  <b>{bal} BDT</b>\n📲 আজকের নম্বর  <b>{n}</b>\n🔑 আজকের OTP  <b>{o}</b></blockquote>\n"
                   "⚡ <i>ইন্সট্যান্ট নম্বর • লাইভ OTP • দ্রুত পেমেন্ট</i>",
        "banned": "🛑 <b>আপনি ব্যান হয়েছেন।</b>",
        "maint": "🛠 <b>বট মেইনটেনেন্সে আছে</b>\nকিছুক্ষণ পর আবার চেষ্টা করুন।",
        "off": "⛔ এডমিন সাময়িকভাবে এই ফিচারটি বন্ধ রেখেছেন।",
        "join": "📌 <b>জয়েন করা বাধ্যতামূলক</b>\n" + LINE + "\nবট ব্যবহার করতে নিচের চ্যানেলে জয়েন করে <b>জয়েন করেছি</b> চাপুন।",
        "joined_btn": "✅ জয়েন করেছি", "not_joined": "❌ আপনি এখনো সব চ্যানেলে জয়েন করেননি!",
        "pick_lang": "🌐 <b>Choose your language / ভাষা বাছাই করুন</b>",
        "lang_set": "✅ ভাষা পরিবর্তন হয়েছে।",
        "svc_title": "📲 <b>সার্ভিস সিলেক্ট করুন</b>\n" + LINE + "\nযে অ্যাপের জন্য নম্বর লাগবে সেটি বাছাই করুন:",
        "svc_none": "⚠️ <b>এখন কোনো লাইভ সার্ভিস নেই</b>\nকিছু সেকেন্ড পর আবার চেষ্টা করুন।",
        "country_title": "🌍 <b>দেশ সিলেক্ট করুন</b>\n" + LINE + "\n🎯 সার্ভিস: <b>{svc}</b>",
        "custom_btn": "⚙️ কাস্টম রেঞ্জ",
        "custom_prompt": "⚙️ <b>কাস্টম রেঞ্জ</b>\n" + LINE + "\nরেঞ্জ পাঠান, যেমন <code>234XXX</code>",
        "bad_range": "🛑 ভুল রেঞ্জ! উদাহরণ: <code>234XXX</code>",
        "alloc_wait": "⏳ <b>প্রিমিয়াম নম্বর বরাদ্দ হচ্ছে…</b>",
        "alloc_fail": "🛑 <b>নম্বর পাওয়া যায়নি</b>\nএই রেঞ্জে এখন কোনো নম্বর খালি নেই। অন্য দেশ চেষ্টা করুন।",
        "card_title": "নম্বর প্রস্তুত", "country": "দেশ", "service": "সার্ভিস", "range": "রেঞ্জ",
        "waiting": "⏳ অপেক্ষা", "reward": "প্রতি OTP রিওয়ার্ড", "scan": "📡 <i>OTP এর জন্য অপেক্ষা চলছে… এখানেই অটো দেখাবে।</i>",
        "got_otp": "✅ OTP এসেছে — নতুন নম্বর লাগলে Change Number চাপুন।",
        "b_change_num": "🔄 নম্বর বদলান", "b_change_cty": "🌍 দেশ বদলান", "b_change_svc": "🧩 সার্ভিস বদলান",
        "b_group": "📢 OTP গ্রুপ", "b_back": "◀️ ফিরে যান", "b_cancel": "✖️ বাতিল", "b_copy_otp": "OTP কপি",
        "b_copy_num": "নম্বর কপি",
        "otp_title": "OTP এসেছে", "credited": "আপনার ওয়ালেটে যোগ হয়েছে",
        "bal_title": "ওয়ালেট", "bal_body": "<blockquote>💵 বর্তমান ব্যালেন্স  <b>{bal} BDT</b>\n📈 মোট আয়  <b>{earned} BDT</b></blockquote>",
        "b_withdraw": "💸 উইথড্র",
        "wd_min": "<blockquote>💰 ব্যালেন্স: <b>{bal} BDT</b>\n📉 সর্বনিম্ন উইথড্র: <b>{min} BDT</b></blockquote>",
        "wd_method": "💳 <b>পেমেন্ট মেথড সিলেক্ট করুন</b>",
        "wd_amount": "🪙 পরিমাণ লিখুন (সর্বনিম্ন {min} • সর্বোচ্চ {max} BDT):",
        "wd_account_bd": "📲 ওয়ালেট নম্বর পাঠান (যেমন <code>017XXXXXXXX</code>):",
        "wd_account_any": "📲 আপনার রিসিভ আইডি / অ্যাড্রেস পাঠান:",
        "wd_summary": "✨ <b>উইথড্র কনফার্ম করুন</b>\n" + LINE + "\n<blockquote>💳 {method}\n📲 {acct}\n🪙 {amount} BDT</blockquote>",
        "wd_ok": "✅ <b>রিকোয়েস্ট জমা হয়েছে</b>\n" + LINE + "\n<blockquote>🆔 <code>{pid}</code>\n💳 {method} • {acct}\n🪙 {amount} BDT</blockquote>\nএডমিন শীঘ্রই রিভিউ করবেন।",
        "wd_cancelled": "❌ উইথড্র বাতিল।", "wd_insufficient": "🚫 ব্যালেন্স পর্যাপ্ত নয়!",
        "wd_bad_amount": "⚠️ {min} থেকে {max} এর মধ্যে সঠিক পরিমাণ দিন।",
        "wd_bad_acct": "⚠️ অ্যাকাউন্ট ফরম্যাট ভুল!",
        "wd_paid": "✅ <b>উইথড্র অ্যাপ্রুভ হয়েছে</b>\n<blockquote>💳 {method} • {acct}\n🪙 {amount} BDT\n🆔 <code>{pid}</code></blockquote>পেমেন্ট পাঠানো হয়েছে। ধন্যবাদ!",
        "wd_rej": "❌ <b>উইথড্র রিজেক্ট হয়েছে</b>\n<blockquote>💳 {method} • {acct}\n🪙 {amount} BDT\n🆔 <code>{pid}</code></blockquote>💰 টাকা ওয়ালেটে ফেরত দেওয়া হয়েছে।",
        "prof_title": "প্রোফাইল",
        "prof_body": "<blockquote>🏷 নাম  <b>{name}</b>\n🆔 আইডি  <code>{uid}</code>\n💰 ব্যালেন্স  <b>{bal} BDT</b>\n👥 রেফারেল  <b>{refs}</b></blockquote>\n"
                     "📊 <b>আজ</b>  📱 {n_t} • 🔑 {o_t}\n🌐 <b>সর্বমোট</b>  📱 {n_a} • 🔑 {o_a}",
        "ref_title": "রেফারেল সেন্টার",
        "ref_body": "<blockquote>👥 ইনভাইট  <b>{refs}</b>\n🎁 প্রতি ইনভাইট বোনাস  <b>{bonus} BDT</b></blockquote>\n🔗 <b>আপনার লিংক</b>\n<code>{link}</code>",
        "ref_btn": "👥 ইনভাইটেড ইউজার", "ref_none": "এখনো কেউ জয়েন করেনি।",
        "top_title": "টপ আর্নার", "fallback": "🔹 নিচের মেনু থেকে অপশন বাছাই করুন:",
        "support": "💬 সাপোর্ট",
    },
}


def tr(uid_or_lang, key, **kw):
    lang = uid_or_lang if uid_or_lang in ("en", "bn") else ulang(uid_or_lang)
    s = T[lang][key]
    return s.format(**kw) if kw else s


def main_kb(lang):
    i = 0 if lang == "en" else 1
    rows = [[KB(MENU["get"][i], "success")],
            [KB(MENU["bal"][i], "primary"), KB(MENU["ref"][i], "primary")],
            [KB(MENU["prof"][i]), KB(MENU["top"][i])],
            [KB(MENU["lang"][i])]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def welcome_text(uid, name):
    lang = ulang(uid)
    u = user_get(uid)
    st = user_stats(uid)
    custom = S("welcome_en" if lang == "en" else "welcome_bn").strip()
    base = custom if custom else T[lang]["welcome"]
    try:
        return base.format(bot=esc(S("bot_name")), name=esc(name), bal=fm(u["balance"]), n=st["n_today"], o=st["o_today"])
    except Exception:
        return base


# =====================================================================
# 🔌 API LAYER
# =====================================================================
http = httpx.AsyncClient(timeout=httpx.Timeout(connect=4.0, read=10.0, write=5.0, pool=4.0),
                         limits=httpx.Limits(max_connections=500, max_keepalive_connections=100))
API_STATUS = {"ok": None, "ts": "—", "services": 0, "err": ""}
_live = {"services": []}


async def api_get(path):
    return await http.get(S("api_base").rstrip("/") + path, headers={"mauthapi": S("api_key")})


async def api_post(path, js):
    return await http.post(S("api_base").rstrip("/") + path, json=js, headers={"mauthapi": S("api_key")})


async def refresh_services():
    try:
        r = await api_get("/api/liveaccess")
        res = r.json()
        if (res.get("meta") or {}).get("status") == "ok":
            _live["services"] = (res.get("data") or {}).get("services", []) or []
            API_STATUS.update(ok=True, ts=datetime.now().strftime("%H:%M:%S"), services=len(_live["services"]), err="")
        else:
            API_STATUS.update(ok=False, ts=datetime.now().strftime("%H:%M:%S"), err=str(res.get("meta"))[:80])
    except Exception as e:
        API_STATUS.update(ok=False, ts=datetime.now().strftime("%H:%M:%S"), err=f"{type(e).__name__}")


def live_services():
    """API থেকে আসা সার্ভিস ∩ DB তে enabled সার্ভিস"""
    enabled = {r["sid"].upper() for r in many("SELECT sid FROM services WHERE enabled=1")}
    return [s for s in _live["services"] if str(s.get("sid", "")).strip().upper() in enabled]


async def services_loop():
    while True:
        await refresh_services()
        await asyncio.sleep(max(5, S("live_interval")))


async def _one_number(rid):
    try:
        r = await api_post("/api/getnum", {"rid": rid})
        d = (r.json().get("data") or {})
        if d.get("full_number"):
            return digits(d["full_number"])
    except Exception as e:
        print(f"getnum error: {e}")
    return None


async def fetch_numbers(range_text, n):
    rid = re.sub(r"[xX]+$", "", range_text.strip())
    got = []
    for attempt in range(3):
        need = n - len(got)
        if need <= 0:
            break
        res = await asyncio.gather(*[_one_number(rid) for _ in range(need + attempt)])
        for num in res:
            if num and num not in got:
                got.append(num)
    return got[:n]


# =====================================================================
# 🧾 NUMBER CARD
# =====================================================================
def batch_rows(batch):
    return many("SELECT * FROM numbers WHERE batch=? ORDER BY id", (batch,))


def number_card(batch, lang):
    rows = batch_rows(batch)
    if not rows:
        return "—", None
    first = rows[0]
    flag, cname = country_of(first["number"])
    got = any(r["otp"] for r in rows)
    lines = []
    for i, r in enumerate(rows):
        status = f"✅ <code>{esc(r['otp'])}</code>" if r["otp"] else T[lang]["waiting"]
        lines.append(f"{NUM_EMOJI[i]} <code>+{r['number']}</code>  {status}")
    text = (head("💎", T[lang]["card_title"]) +
            f"🌍 {T[lang]['country']}  <b>{flag} {esc(cname)}</b>\n"
            f"📱 {T[lang]['service']}  <b>{logo(first['sid'])}</b>\n"
            f"📡 {T[lang]['range']}  <code>{esc(first['rng'])}</code>\n\n"
            f"<blockquote>" + "\n".join(lines) + "</blockquote>\n"
            f"💰 {T[lang]['reward']}: <b>+{fm(S('otp_rate'))} BDT</b>\n" +
            (T[lang]["got_otp"] if got else T[lang]["scan"]))
    kb = []
    for r in rows:
        row = [B(f"📋 +{r['number']}", copy=f"+{r['number']}", style="primary")]
        if r["otp"]:
            row.append(B(f"🔑 {r['otp']}", copy=r["otp"], style="success"))
        kb.append(row)
    kb.append([B(T[lang]["b_change_num"], f"chg:{batch}", style="success"),
               B(T[lang]["b_change_cty"], f"chc:{batch}", style="primary")])
    last = [B(T[lang]["b_change_svc"], "svc")]
    if valid_url(S("otp_group_link")):
        last.append(B(T[lang]["b_group"], url=S("otp_group_link"), style="primary"))
    kb.append(last)
    return text, InlineKeyboardMarkup(kb)


async def safe_edit_msg(bot, chat_id, msg_id, text, kb=None):
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, parse_mode="HTML",
                                    reply_markup=kb, disable_web_page_preview=True)
        return True
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            print(f"edit error: {e}")
    except Exception as e:
        print(f"edit error: {e}")
    return False


async def allocate(bot, uid, chat_id, msg_id, sid, range_text):
    """নম্বর বরাদ্দ করে (msg_id মেসেজটি এডিট করে) কার্ড দেখায়।"""
    lang = ulang(uid)
    await safe_edit_msg(bot, chat_id, msg_id, T[lang]["alloc_wait"])
    n = max(1, min(6, int(S("numbers_per_req"))))
    nums = await fetch_numbers(range_text, n)
    if not nums:
        kb = InlineKeyboardMarkup([[B(T[lang]["b_change_cty"], f"s:{sid}") if sid else B(T[lang]["b_back"], "svc"),
                                    B(T[lang]["b_change_svc"], "svc")]])
        await safe_edit_msg(bot, chat_id, msg_id, T[lang]["alloc_fail"], kb)
        return
    batch = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    ts = now_iso()
    for num in nums:
        xc("INSERT INTO numbers(batch,uid,number,rng,sid,chat_id,msg_id,created) VALUES(?,?,?,?,?,?,?,?)",
           (batch, uid, num, range_text, sid or "", chat_id, msg_id, ts))
    log(uid, "NUMBERS", f"{len(nums)} x {range_text}")
    text, kb = number_card(batch, lang)
    await safe_edit_msg(bot, chat_id, msg_id, text, kb)


# =====================================================================
# 🔁 OTP MONITOR
# =====================================================================
async def handle_otp(bot, o):
    num = digits(o.get("number", ""))
    if not num:
        return
    sms = o.get("message") or o.get("otp") or o.get("sms") or "No SMS Content"
    code = extract_otp(sms)
    key = str(o.get("otp_id", "")) or f"{num}_{sms}"
    cutoff = (datetime.now() - timedelta(minutes=int(S("number_ttl")))).isoformat(timespec="seconds")
    row = one("SELECT * FROM numbers WHERE number=? AND created>=? ORDER BY id DESC LIMIT 1", (num, cutoff))
    if not row:
        return
    reward = float(S("otp_rate"))
    if xc("INSERT OR IGNORE INTO otps(key,uid,number,otp,reward,ts) VALUES(?,?,?,?,?,?)",
          (key, row["uid"], num, code, reward, now_iso())) == 0:
        return  # আগেই পেমেন্ট হয়েছে
    uid = row["uid"]
    add_balance(uid, reward)
    xc("UPDATE numbers SET otp=?, sms=?, otp_at=? WHERE id=?", (code, str(sms)[:500], now_iso(), row["id"]))
    log(uid, "OTP", f"{num} {code}")

    lang = ulang(uid)
    flag, cname = country_of(num)
    sid = row["sid"] or detect_service(sms)
    body = (f"{flag} <b>{esc(cname)}</b> • {logo(sid or 'SMS')}\n"
            f"📟 <code>+{num}</code>\n🔑 <code>{esc(code)}</code>\n"
            f"<blockquote><code>{esc(sms)}</code></blockquote>")
    # ইউজারকে মেসেজ
    try:
        await bot.send_message(
            uid, head("🔑", T[lang]["otp_title"]) + body + f"\n💰 <b>+{fm(reward)} BDT</b> {T[lang]['credited']}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[B(f"🔑 {T[lang]['b_copy_otp']}", copy=code, style="success"),
                                                B(f"📟 {T[lang]['b_copy_num']}", copy=f"+{num}", style="primary")]]))
    except Exception as e:
        print(f"user notify fail: {e}")
    # কার্ড আপডেট (শুধু যদি এই ব্যাচ-ই এখনো স্ক্রিনে থাকে)
    latest = scalar("SELECT batch FROM numbers WHERE chat_id=? AND msg_id=? ORDER BY id DESC LIMIT 1",
                    (row["chat_id"], row["msg_id"]))
    if latest == row["batch"]:
        text, kb = number_card(row["batch"], lang)
        await safe_edit_msg(bot, row["chat_id"], row["msg_id"], text, kb)
    # গ্রুপ পোস্ট
    gid = str(S("otp_group_id")).strip()
    if S("group_post_on") and gid:
        shown = f"+{mask(num)}" if S("mask_group") else f"+{num}"
        gbody = (head("💎", "LIVE OTP") + f"{flag} <b>{esc(cname)}</b>\n📱 {logo(sid or 'SMS')}\n"
                 f"📟 <code>{shown}</code>\n🔑 <code>{esc(code)}</code>\n<blockquote><code>{esc(sms)}</code></blockquote>")
        btns = []
        if valid_url(S("panel_link")):
            btns.append(B("🤖 PANEL", url=S("panel_link"), style="primary"))
        if valid_url(S("channel_link")):
            btns.append(B("📢 CHANNEL", url=S("channel_link"), style="success"))
        try:
            await bot.send_message(int(gid) if gid.lstrip("-").isdigit() else gid, gbody, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup([btns]) if btns else None)
        except Exception as e:
            print(f"group post fail: {e}")


async def monitor_loop(app):
    while True:
        try:
            r = await api_get("/api/success-otp")
            otps = ((r.json().get("data") or {}).get("otps")) or []
            for o in otps:
                try:
                    await handle_otp(app.bot, o)
                except Exception as e:
                    print(f"handle_otp error: {e}")
        except Exception as e:
            print(f"Monitor error: {type(e).__name__}: {e}")
        await asyncio.sleep(max(0.5, float(S("check_interval"))))


# =====================================================================
# 🚦 GATE (ban / maintenance / force-join)
# =====================================================================
_join_ok = {}


async def missing_channels(bot, uid):
    if not S("force_join_on"):
        return []
    if _join_ok.get(uid, 0) > datetime.now().timestamp():
        return []
    miss = []
    for ch in many("SELECT * FROM channels"):
        chat = ch["chat"]
        try:
            m = await bot.get_chat_member(int(chat) if chat.lstrip("-").isdigit() else chat, uid)
            if m.status in ("left", "kicked"):
                miss.append(ch)
        except Exception as e:
            print(f"join check error ({chat}): {e}")  # বট অ্যাডমিন না হলে আটকাবে না
    if not miss:
        _join_ok[uid] = datetime.now().timestamp() + 120
    return miss


def join_card(lang, miss):
    kb = [[B(f"📢 Join {i+1}", url=c["link"], style="primary")] for i, c in enumerate(miss) if valid_url(c["link"])]
    kb.append([B(T[lang]["joined_btn"], "fj", style="success")])
    return T[lang]["join"], InlineKeyboardMarkup(kb)


async def gate(update, context, allow_join_cb=False):
    user = update.effective_user
    if not user:
        return False
    uid = user.id
    if is_admin(uid):
        return False
    u = user_get(uid, user)
    lang = u.get("lang") or "bn"
    if u["banned"]:
        await context.bot.send_message(uid, T[lang]["banned"], parse_mode="HTML")
        return True
    if S("maintenance"):
        await context.bot.send_message(uid, T[lang]["maint"], parse_mode="HTML")
        return True
    if not allow_join_cb:
        miss = await missing_channels(context.bot, uid)
        if miss:
            text, kb = join_card(lang, miss)
            await context.bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb)
            return True
    return False


# =====================================================================
# 🧭 USER FLOWS
# =====================================================================
def services_kb(lang):
    svcs = live_services()
    rows = []
    for i in range(0, len(svcs), 2):
        row = []
        for s in svcs[i:i + 2]:
            sid = str(s.get("sid", "")).strip()
            row.append(B(f"🚀 {sid} ({len(s.get('ranges', []))})", f"s:{sid}", style="primary"))
        rows.append(row)
    if S("custom_range_on"):
        rows.append([B(T[lang]["custom_btn"], "cr", style="success")])
    return InlineKeyboardMarkup(rows)


async def show_services(update_or_query, context, edit=False):
    uid = (getattr(update_or_query, "effective_user", None) or update_or_query.from_user).id
    lang = ulang(uid)
    if not S("getnum_on") and not is_admin(uid):
        text, kb = T[lang]["off"], None
    else:
        if not live_services():
            await refresh_services()
        if not live_services():
            text, kb = T[lang]["svc_none"], None
        else:
            text, kb = T[lang]["svc_title"], services_kb(lang)
    if edit:
        await safe_edit_msg(context.bot, update_or_query.message.chat_id, update_or_query.message.message_id, text, kb)
    else:
        await update_or_query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


def countries_kb(sid, lang):
    svc = next((s for s in live_services() if str(s.get("sid", "")).strip().upper() == sid.upper()), None)
    if not svc:
        return None
    btns, seen = [], set()
    for r in (svc.get("ranges") or [])[:40]:
        flag, cname = country_of(re.sub(r"[xX]+$", "", str(r)))
        label = f"{flag} {cname}"
        if label in seen:
            continue
        seen.add(label)
        btns.append(B(label, f"r:{sid}:{r}", style="primary"))
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([B(T[lang]["b_back"], "svc", style="danger")])
    return InlineKeyboardMarkup(rows)


async def show_countries(query, context, sid):
    lang = ulang(query.from_user.id)
    kb = countries_kb(sid, lang)
    if not kb:
        await show_services(query, context, edit=True)
        return
    await safe_edit_msg(context.bot, query.message.chat_id, query.message.message_id,
                        T[lang]["country_title"].format(svc=logo(sid)), kb)


async def send_menu(bot, uid, name):
    await bot.send_message(uid, welcome_text(uid, name), parse_mode="HTML", reply_markup=main_kb(ulang(uid)))
    if valid_url(S("support_link")):
        await bot.send_message(uid, "👇", reply_markup=InlineKeyboardMarkup(
            [[B(T[ulang(uid)]["support"], url=S("support_link"), style="primary")]]))


def lang_picker():
    return InlineKeyboardMarkup([[B("🇺🇸 English", "lang:en", style="primary"), B("🇧🇩 বাংলা", "lang:bn", style="success")]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    uid = tg.id
    existed = scalar("SELECT 1 FROM users WHERE uid=?", (uid,)) == 1
    u = user_get(uid, tg)
    if await gate(update, context):
        return
    args = context.args or []
    if args:
        p = args[0]
        if "X" in p.upper() and u.get("lang"):
            m = await update.message.reply_text(T[ulang(uid)]["alloc_wait"], parse_mode="HTML")
            await allocate(context.bot, uid, m.chat_id, m.message_id, "", p.upper())
            return
        if p.isdigit() and not existed and int(p) != uid and scalar("SELECT 1 FROM users WHERE uid=?", (int(p),)) == 1:
            xc("UPDATE users SET referred_by=? WHERE uid=?", (int(p), uid))
            xc("UPDATE users SET ref_count=ref_count+1 WHERE uid=?", (int(p),))
            bonus = float(S("ref_bonus"))
            if bonus > 0:
                add_balance(int(p), bonus)
            log(int(p), "REFERRAL", uid)
            try:
                await context.bot.send_message(
                    int(p), f"🎉 <b>New referral!</b>\n<blockquote>🆔 <code>{uid}</code>\n🎁 +{fm(bonus)} BDT</blockquote>",
                    parse_mode="HTML")
            except Exception:
                pass
    if not u.get("lang"):
        await update.message.reply_text(T["en"]["pick_lang"], parse_mode="HTML", reply_markup=lang_picker())
        return
    await send_menu(context.bot, uid, tg.full_name)
    if is_admin(uid):
        await update.message.reply_text("🛡 Admin panel: /admin")


async def language_cmd(update, context):
    if await gate(update, context):
        return
    await update.message.reply_text(T["en"]["pick_lang"], parse_mode="HTML", reply_markup=lang_picker())


async def withdraw_methods_kb(lang):
    rows = [[B(f"💳 {m['name']}", f"wdm:{m['name']}", style="primary")] for m in many("SELECT name FROM methods WHERE enabled=1")]
    rows.append([B(T[lang]["b_cancel"], "x", style="danger")])
    return InlineKeyboardMarkup(rows)


async def route_menu(update, context, key):
    uid = update.effective_user.id
    lang = ulang(uid)
    u = user_get(uid)
    if key == "get":
        await show_services(update, context)
    elif key == "bal":
        await update.message.reply_text(
            head("💰", T[lang]["bal_title"]) + T[lang]["bal_body"].format(bal=fm(u["balance"]), earned=fm(u["total_earned"])),
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[B(T[lang]["b_withdraw"], "wd", style="success")]]))
    elif key == "ref":
        bi = await context.bot.get_me()
        link = f"https://t.me/{bi.username}?start={uid}"
        await update.message.reply_text(
            head("🎁", T[lang]["ref_title"]) + T[lang]["ref_body"].format(refs=u["ref_count"], bonus=fm(S("ref_bonus")), link=link),
            parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[B(T[lang]["ref_btn"], "refs", style="primary")]]))
    elif key == "prof":
        st = user_stats(uid)
        await update.message.reply_text(
            head("👤", T[lang]["prof_title"]) + T[lang]["prof_body"].format(
                name=esc(update.effective_user.full_name), uid=uid, bal=fm(u["balance"]), refs=u["ref_count"],
                n_t=st["n_today"], o_t=st["o_today"], n_a=st["n_all"], o_a=st["o_all"]), parse_mode="HTML")
    elif key == "top":
        rows = many("SELECT name,total_earned FROM users WHERE total_earned>0 ORDER BY total_earned DESC LIMIT 10")
        medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
        lines = [f"{medals[i]} <b>{esc((r['name'] or 'User')[:2])}***</b> — {fm(r['total_earned'])} BDT" for i, r in enumerate(rows)]
        await update.message.reply_text(head("🏆", T[lang]["top_title"]) + ("\n".join(lines) or "—"), parse_mode="HTML")
    elif key == "lang":
        await update.message.reply_text(T["en"]["pick_lang"], parse_mode="HTML", reply_markup=lang_picker())


def acct_is_bd(method):
    return "BINANCE" not in method.upper()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    tg = update.effective_user
    uid = tg.id
    text = update.message.text.strip()
    user_get(uid, tg)
    if await gate(update, context):
        return
    ud = context.user_data
    lang = ulang(uid)

    if is_admin(uid) and ud.get("adm"):
        await admin_text_input(update, context)
        return

    # মেনু বাটন সবসময় আগে (স্টেট বাতিল করে)
    if text in LABEL2KEY:
        ud.pop("state", None)
        ud.pop("adm", None)
        await route_menu(update, context, LABEL2KEY[text])
        return

    state = ud.get("state")
    if state == "custom":
        ud.pop("state", None)
        rng = text.upper().replace(" ", "")
        if not re.search(r"\d", rng):
            await update.message.reply_text(T[lang]["bad_range"], parse_mode="HTML")
            return
        m = await update.message.reply_text(T[lang]["alloc_wait"], parse_mode="HTML")
        await allocate(context.bot, uid, m.chat_id, m.message_id, "", rng)
        return

    if state == "wd_amount":
        try:
            amount = round(float(text), 2)
        except ValueError:
            amount = -1
        mn, mx = float(S("min_wd")), float(S("max_wd"))
        if amount < mn or amount > mx:
            await update.message.reply_text(T[lang]["wd_bad_amount"].format(min=fm(mn), max=fm(mx)))
            return
        if amount > user_get(uid)["balance"]:
            await update.message.reply_text(T[lang]["wd_insufficient"])
            return
        ud["wd"]["amount"] = amount
        ud["state"] = "wd_account"
        k = "wd_account_bd" if acct_is_bd(ud["wd"]["method"]) else "wd_account_any"
        await update.message.reply_text(T[lang][k], parse_mode="HTML",
                                        reply_markup=InlineKeyboardMarkup([[B(T[lang]["b_cancel"], "x", style="danger")]]))
        return

    if state == "wd_account":
        method = ud["wd"]["method"]
        if acct_is_bd(method):
            d = digits(text)
            ok = d.startswith("01") and len(d) in ((11, 12) if method.upper() == "ROCKET" else (11,))
            acct = d
        else:
            acct = text.strip()
            ok = 4 <= len(acct) <= 120
        if not ok:
            await update.message.reply_text(T[lang]["wd_bad_acct"])
            return
        ud["wd"]["account"] = acct
        ud["state"] = None
        await update.message.reply_text(
            T[lang]["wd_summary"].format(method=esc(method), acct=esc(acct), amount=fm(ud["wd"]["amount"])), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[B("❌", "x", style="danger"), B("✅ Confirm", "wdc", style="success")]]))
        return

    await update.message.reply_text(T[lang]["fallback"], reply_markup=main_kb(lang))


async def notify_admins(bot, text, kb=None):
    for aid in admin_ids():
        try:
            await bot.send_message(aid, text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass


# =====================================================================
# 🔘 CALLBACKS (USER)
# =====================================================================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data or ""
    ud = context.user_data
    if data.startswith("a:"):
        await q.answer()
        await admin_callback(update, context)
        return
    user_get(uid, q.from_user)
    if data == "fj":
        _join_ok.pop(uid, None)
        miss = await missing_channels(context.bot, uid)
        lang = ulang(uid)
        if miss:
            await q.answer(T[lang]["not_joined"], show_alert=True)
            return
        await q.answer("✅")
        try:
            await q.message.delete()
        except Exception:
            pass
        await send_menu(context.bot, uid, q.from_user.full_name)
        return
    await q.answer()
    if data.startswith("lang:"):
        code = data.split(":")[1]
        first = not user_get(uid).get("lang")
        xc("UPDATE users SET lang=? WHERE uid=?", (code, uid))
        try:
            await q.message.delete()
        except Exception:
            pass
        await send_menu(context.bot, uid, q.from_user.full_name)
        return
    if await gate(update, context):
        return
    lang = ulang(uid)
    chat_id, msg_id = q.message.chat_id, q.message.message_id

    if data == "x":
        ud.pop("state", None)
        ud.pop("wd", None)
        await safe_edit_msg(context.bot, chat_id, msg_id, T[lang]["wd_cancelled"])
    elif data == "svc":
        await show_services(q, context, edit=True)
    elif data.startswith("s:"):
        if not S("getnum_on") and not is_admin(uid):
            await q.answer(T[lang]["off"], show_alert=True)
            return
        await show_countries(q, context, data[2:])
    elif data.startswith("r:"):
        if not S("getnum_on") and not is_admin(uid):
            await q.answer(T[lang]["off"], show_alert=True)
            return
        _, sid, rng = data.split(":", 2)
        asyncio.create_task(allocate(context.bot, uid, chat_id, msg_id, sid, rng))
    elif data.startswith("chg:"):
        row = one("SELECT * FROM numbers WHERE batch=? AND uid=? LIMIT 1", (data[4:], uid))
        if not row:
            await q.answer("Expired", show_alert=True)
            return
        if not S("getnum_on") and not is_admin(uid):
            await q.answer(T[lang]["off"], show_alert=True)
            return
        asyncio.create_task(allocate(context.bot, uid, chat_id, msg_id, row["sid"], row["rng"]))
    elif data.startswith("chc:"):
        row = one("SELECT * FROM numbers WHERE batch=? AND uid=? LIMIT 1", (data[4:], uid))
        if row and row["sid"]:
            await show_countries(q, context, row["sid"])
        else:
            await show_services(q, context, edit=True)
    elif data == "cr":
        if not S("custom_range_on") and not is_admin(uid):
            await q.answer(T[lang]["off"], show_alert=True)
            return
        ud["state"] = "custom"
        await safe_edit_msg(context.bot, chat_id, msg_id, T[lang]["custom_prompt"],
                            InlineKeyboardMarkup([[B(T[lang]["b_back"], "svc", style="danger")]]))
    elif data.startswith("cp:"):
        await context.bot.send_message(uid, f"<code>{esc(data[3:])}</code>", parse_mode="HTML")
    elif data == "refs":
        rows = many("SELECT uid,name FROM users WHERE referred_by=? ORDER BY joined DESC LIMIT 20", (uid,))
        txt = "\n".join(f"{i+1}. {esc(r['name'] or r['uid'])} • <code>{r['uid']}</code>" for i, r in enumerate(rows)) or T[lang]["ref_none"]
        await context.bot.send_message(uid, head("👥", T[lang]["ref_btn"]) + txt, parse_mode="HTML")
    elif data == "wd":
        if not S("withdraw_on"):
            await q.answer(T[lang]["off"], show_alert=True)
            return
        bal = user_get(uid)["balance"]
        if bal < float(S("min_wd")):
            await context.bot.send_message(uid, T[lang]["wd_min"].format(bal=fm(bal), min=fm(S("min_wd"))), parse_mode="HTML")
            return
        await context.bot.send_message(uid, T[lang]["wd_method"], parse_mode="HTML", reply_markup=await withdraw_methods_kb(lang))
    elif data.startswith("wdm:"):
        ud["wd"] = {"method": data[4:]}
        ud["state"] = "wd_amount"
        await safe_edit_msg(context.bot, chat_id, msg_id, T[lang]["wd_amount"].format(min=fm(S("min_wd")), max=fm(S("max_wd"))),
                            InlineKeyboardMarkup([[B(T[lang]["b_cancel"], "x", style="danger")]]))
    elif data == "wdc":
        w = ud.pop("wd", None)
        ud.pop("state", None)
        if not w or "account" not in w:
            await safe_edit_msg(context.bot, chat_id, msg_id, "⚠️ Expired.")
            return
        # অ্যাটমিক ডেবিট — ডাবল ক্লিকে দুবার কাটবে না
        if xc("UPDATE users SET balance=ROUND(balance-?,2) WHERE uid=? AND balance>=?", (w["amount"], uid, w["amount"])) == 0:
            await safe_edit_msg(context.bot, chat_id, msg_id, T[lang]["wd_insufficient"])
            return
        pid = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
        xc("INSERT INTO withdraws(pid,uid,method,amount,account,status,created) VALUES(?,?,?,?,?,'pending',?)",
           (pid, uid, w["method"], w["amount"], w["account"], now_iso()))
        log(uid, "WITHDRAW", f"{pid} {w['amount']}")
        await safe_edit_msg(context.bot, chat_id, msg_id,
                            T[lang]["wd_ok"].format(pid=pid, method=esc(w["method"]), acct=esc(w["account"]), amount=fm(w["amount"])))
        await notify_admins(context.bot,
                            f"💳 <b>NEW WITHDRAW</b>\n<blockquote>👤 <code>{uid}</code> {esc(q.from_user.full_name)}\n"
                            f"🏦 {esc(w['method'])} • <code>{esc(w['account'])}</code>\n🪙 {fm(w['amount'])} BDT</blockquote>",
                            InlineKeyboardMarkup([[B("👁 Review", f"a:wdv:{pid}", style="primary")]]))


# =====================================================================
# 🛡 ADMIN PANEL
# =====================================================================
HOME = [B("🏠 Home", "a:home")]
USERS_PER_PAGE = 8


def dash():
    t0 = today0()
    users = scalar("SELECT COUNT(*) FROM users")
    banned = scalar("SELECT COUNT(*) FROM users WHERE banned=1")
    new_t = scalar("SELECT COUNT(*) FROM users WHERE joined>=?", (t0,))
    active = scalar("SELECT COUNT(DISTINCT uid) FROM numbers WHERE created>=?", (t0,))
    n_t, n_a = scalar("SELECT COUNT(*) FROM numbers WHERE created>=?", (t0,)), scalar("SELECT COUNT(*) FROM numbers")
    o_t, o_a = scalar("SELECT COUNT(*) FROM otps WHERE ts>=?", (t0,)), scalar("SELECT COUNT(*) FROM otps")
    r_t = scalar("SELECT COALESCE(SUM(reward),0) FROM otps WHERE ts>=?", (t0,))
    bal = scalar("SELECT COALESCE(SUM(balance),0) FROM users")
    pend = one("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM withdraws WHERE status='pending'")
    paid = one("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM withdraws WHERE status='paid'")
    api = {True: "🟢 OK", False: "🔴 FAIL", None: "⚪ …"}[API_STATUS["ok"]]
    text = (head("🛡", "ADMIN CONTROL CENTER") + f"🕒 <i>{datetime.now().strftime('%d/%m/%Y %I:%M %p')}</i>\n\n"
            f"👥 <b>Users</b>\n<blockquote>Total <code>{users}</code> • New today <code>{new_t}</code>\n"
            f"Active today <code>{active}</code> • Banned <code>{banned}</code></blockquote>\n"
            f"📊 <b>Activity</b>\n<blockquote>Today 📱 <code>{n_t}</code> • 🔑 <code>{o_t}</code> • 💰 <code>{fm(r_t)}</code>\n"
            f"All-time 📱 <code>{n_a}</code> • 🔑 <code>{o_a}</code></blockquote>\n"
            f"💰 <b>Finance</b>\n<blockquote>Wallets <code>{fm(bal)} BDT</code>\n"
            f"⏳ Pending <code>{pend['c']}</code> (<code>{fm(pend['s'])}</code>)\n"
            f"✅ Paid <code>{paid['c']}</code> (<code>{fm(paid['s'])}</code>)</blockquote>\n"
            f"🔌 API {api} • services <code>{API_STATUS['services']}</code> • <code>{API_STATUS['ts']}</code>"
            + (f"\n⚠️ <code>{esc(API_STATUS['err'])}</code>" if API_STATUS["err"] else "") +
            ("\n🛠 <b>MAINTENANCE ON</b>" if S("maintenance") else ""))
    kb = InlineKeyboardMarkup([
        [B(f"💳 Withdrawals ({pend['c']})", "a:wd", style="primary"), B("👥 Users", "a:users:0", style="primary")],
        [B("📢 Broadcast", "a:bc", style="success"), B("⚙️ Settings", "a:set")],
        [B("🧩 Services", "a:svcs"), B("💳 Methods", "a:wm")],
        [B("📌 Force Join", "a:ch"), B("🛡 Admins", "a:adm")],
        [B("🏆 Top", "a:top"), B("📜 Logs", "a:logs"), B("🔌 API Test", "a:api")],
        [B("📤 Backup DB", "a:backup"), B("📄 Users CSV", "a:csv")],
        [B("🔄 Refresh", "a:home", style="success")]])
    return text, kb


def users_page(page):
    total = scalar("SELECT COUNT(*) FROM users")
    pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    page = max(0, min(page, pages - 1))
    rows = many("SELECT uid,name,balance,banned FROM users ORDER BY balance DESC LIMIT ? OFFSET ?", (USERS_PER_PAGE, page * USERS_PER_PAGE))
    kb = [[B(f"{'🚫' if r['banned'] else '👤'} {(r['name'] or str(r['uid']))[:16]} • {fm(r['balance'])}", f"a:u:{r['uid']}")] for r in rows]
    nav = []
    if page > 0:
        nav.append(B("⬅️", f"a:users:{page-1}"))
    nav.append(B(f"{page+1}/{pages}", "a:noop"))
    if page < pages - 1:
        nav.append(B("➡️", f"a:users:{page+1}"))
    kb.append(nav)
    kb.append([B("🔍 Search", "a:search", style="primary"), *HOME])
    return head("👥", f"USERS — {total}") + "<i>Sorted by balance</i>", InlineKeyboardMarkup(kb)


def user_card(uid):
    u = one("SELECT * FROM users WHERE uid=?", (int(uid),))
    if not u:
        return "❌ User not found.", InlineKeyboardMarkup([HOME])
    st = user_stats(u["uid"])
    paid = scalar("SELECT COALESCE(SUM(amount),0) FROM withdraws WHERE uid=? AND status='paid'", (u["uid"],))
    text = (head("👤", "USER") + ("🚫 <b>BANNED</b>\n" if u["banned"] else "") +
            f"<blockquote>🏷 {esc(u['name'] or '—')}  {('@' + esc(u['username'])) if u['username'] else ''}\n🆔 <code>{u['uid']}</code> • 🌐 {u['lang'] or '—'}\n"
            f"📅 {esc((u['joined'] or '')[:10])}</blockquote>\n"
            f"💵 Balance <b>{fm(u['balance'])}</b> • 📈 Earned <b>{fm(u['total_earned'])}</b>\n"
            f"💸 Paid out <b>{fm(paid)}</b> • 👥 Refs <b>{u['ref_count']}</b>\n"
            f"Today 📱 {st['n_today']} 🔑 {st['o_today']} • All 📱 {st['n_all']} 🔑 {st['o_all']}")
    kb = InlineKeyboardMarkup([
        [B("➕ Add", f"a:ba:{u['uid']}", style="success"), B("➖ Deduct", f"a:bs:{u['uid']}", style="danger")],
        [B("✅ Unban" if u["banned"] else "🚫 Ban", f"a:ban:{u['uid']}", style="success" if u["banned"] else "danger"),
         B("💬 Message", f"a:msg:{u['uid']}", style="primary")],
        [B("⬅️ Users", "a:users:0"), *HOME]])
    return text, kb


def wd_list():
    rows = many("SELECT * FROM withdraws WHERE status='pending' ORDER BY created LIMIT 12")
    kb = [[B(f"⏳ {r['method']} • {fm(r['amount'])} • {r['uid']}", f"a:wdv:{r['pid']}")] for r in rows]
    kb.append([B("📚 History", "a:wdh"), *HOME])
    total = scalar("SELECT COUNT(*) FROM withdraws WHERE status='pending'")
    return head("💳", f"PENDING WITHDRAWALS — {total}") + ("Tap a request to review." if rows else "✅ Nothing pending."), InlineKeyboardMarkup(kb)


def wd_view(pid):
    w = one("SELECT * FROM withdraws WHERE pid=?", (pid,))
    if not w:
        return "❌ Not found.", InlineKeyboardMarkup([[B("⬅️ Back", "a:wd")]])
    u = one("SELECT name,balance FROM users WHERE uid=?", (w["uid"],)) or {}
    icon = {"pending": "⏳ PENDING", "paid": "✅ PAID", "rejected": "❌ REJECTED"}.get(w["status"], w["status"])
    text = (head("💳", f"WITHDRAW — {icon}") +
            f"<blockquote>🆔 <code>{pid}</code>\n👤 <code>{w['uid']}</code> {esc(u.get('name', ''))}\n🏦 {esc(w['method'])}\n"
            f"📞 <code>{esc(w['account'])}</code>\n🪙 <b>{fm(w['amount'])} BDT</b>\n🕒 {esc(w['created'])}</blockquote>"
            f"💵 User balance: {fm(u.get('balance', 0))}")
    kb = []
    if w["status"] == "pending":
        kb.append([B("✅ Approve", f"a:wda:{pid}", style="success"), B("❌ Reject+Refund", f"a:wdr:{pid}", style="danger")])
    kb.append([B("👤 User", f"a:u:{w['uid']}"), B("⬅️ Back", "a:wd")])
    return text, InlineKeyboardMarkup(kb)


def settings_menu():
    kb = [[B(lbl, f"a:sg:{g}")] for g, lbl in GROUPS]
    kb.append(HOME)
    return head("⚙️", "SETTINGS") + "<i>বদলালে সঙ্গে সঙ্গে কার্যকর — রিস্টার্ট লাগে না।</i>", InlineKeyboardMarkup(kb)


def settings_group(g):
    kb = []
    for key, (d, label, typ, grp) in SETTINGS.items():
        if grp != g:
            continue
        v = S(key)
        if typ == "bool":
            kb.append([B(f"{label}: {'🟢 ON' if v else '🔴 OFF'}", f"a:st:{key}")])
        else:
            sv = str(v) if str(v) else "—"
            if key == "api_key" and v:
                sv = v[:3] + "***" + v[-2:]
            kb.append([B(f"{label}: {sv[:28]}", f"a:sk:{key}")])
    kb.append([B("⬅️ Settings", "a:set"), *HOME])
    title = dict(GROUPS)[g]
    return head("⚙️", title) + "Tap a setting to change it.", InlineKeyboardMarkup(kb)


def services_menu():
    live = {str(s.get("sid", "")).upper() for s in _live["services"]}
    rows = many("SELECT * FROM services ORDER BY sid")
    kb = []
    for r in rows:
        dot = "🟢" if r["sid"].upper() in live else "⚪"
        kb.append([B(f"{'✅' if r['enabled'] else '⛔'} {r['sid']} {dot}", f"a:svt:{r['sid']}"), B("🗑", f"a:svd:{r['sid']}", style="danger")])
    kb.append([B("➕ Add service", "a:sva", style="success"), *HOME])
    return head("🧩", "SERVICES") + "✅ enabled • ⛔ hidden • 🟢 live in API now\nসার্ভিসের নাম API-র <code>sid</code> এর সাথে মিলতে হবে।", InlineKeyboardMarkup(kb)


def methods_menu():
    rows = many("SELECT * FROM methods ORDER BY name")
    kb = [[B(f"{'✅' if r['enabled'] else '⛔'} {r['name']}", f"a:wmt:{r['name']}"), B("🗑", f"a:wmd:{r['name']}", style="danger")] for r in rows]
    kb.append([B("➕ Add method", "a:wma", style="success"), *HOME])
    return head("💳", "WITHDRAW METHODS") + "BINANCE নামের মেথডে যেকোনো ID নেওয়া হয়; বাকিগুলোতে BD নম্বর।", InlineKeyboardMarkup(kb)


def channels_menu():
    rows = many("SELECT * FROM channels")
    kb = [[B(f"📢 {r['chat']}", "a:noop"), B("🗑", f"a:chd:{r['id']}", style="danger")] for r in rows]
    kb.append([B("➕ Add channel", "a:cha", style="success"), B(f"Force join: {'🟢' if S('force_join_on') else '🔴'}", "a:st:force_join_on")])
    kb.append(HOME)
    return head("📌", "FORCE JOIN") + "বটকে চ্যানেলে <b>অ্যাডমিন</b> বানাতে হবে।", InlineKeyboardMarkup(kb)


def admins_menu():
    rows = many("SELECT uid FROM admins ORDER BY uid")
    kb = []
    for r in rows:
        tag = "👑 Owner" if is_owner(r["uid"]) else "🛡 Admin"
        row = [B(f"{tag} • {r['uid']}", "a:noop")]
        if not is_owner(r["uid"]):
            row.append(B("🗑", f"a:admd:{r['uid']}", style="danger"))
        kb.append(row)
    kb.append([B("➕ Add admin", "a:adma", style="success"), *HOME])
    return head("🛡", "ADMINS"), InlineKeyboardMarkup(kb)


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data.pop("adm", None)
    t, kb = dash()
    await update.message.reply_text(t, parse_mode="HTML", reply_markup=kb)


async def run_broadcast(bot, admin_id, text):
    users = many("SELECT uid FROM users WHERE banned=0")
    sent = failed = 0
    for u in users:
        for _ in range(2):
            try:
                await bot.send_message(u["uid"], text, parse_mode="HTML", disable_web_page_preview=True)
                sent += 1
                break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                failed += 1
                break
        await asyncio.sleep(0.05)
    log(admin_id, "BROADCAST", f"sent={sent} failed={failed}")
    await bot.send_message(admin_id, f"📢 <b>BROADCAST DONE</b>\n✅ Sent <code>{sent}</code>\n❌ Failed <code>{failed}</code>", parse_mode="HTML")


async def adm_edit(q, text, kb=None):
    await safe_edit_msg(q.get_bot(), q.message.chat_id, q.message.message_id, text, kb)


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    aid = q.from_user.id
    if not is_admin(aid):
        await q.answer("⛔ Admin only", show_alert=True)
        return
    ud = context.user_data
    p = q.data.split(":")
    cmd = p[1]
    arg = p[2] if len(p) > 2 else ""
    arg2 = ":".join(p[3:]) if len(p) > 3 else ""

    if cmd == "noop":
        return
    if cmd == "home":
        ud.pop("adm", None)
        await adm_edit(q, *dash())
    elif cmd == "users":
        await adm_edit(q, *users_page(int(arg or 0)))
    elif cmd == "search":
        ud["adm"] = {"mode": "search"}
        await adm_edit(q, head("🔍", "SEARCH USER") + "User ID অথবা @username পাঠান:", InlineKeyboardMarkup([HOME]))
    elif cmd == "u":
        await adm_edit(q, *user_card(arg))
    elif cmd in ("ba", "bs"):
        ud["adm"] = {"mode": "bal_add" if cmd == "ba" else "bal_sub", "target": int(arg)}
        await adm_edit(q, head("💵", "BALANCE") + f"User <code>{arg}</code>\nকত BDT {'যোগ' if cmd == 'ba' else 'কাটা'} হবে?",
                       InlineKeyboardMarkup([[B("✖️ Cancel", f"a:u:{arg}")]]))
    elif cmd == "ban":
        uid = int(arg)
        cur = scalar("SELECT banned FROM users WHERE uid=?", (uid,))
        xc("UPDATE users SET banned=? WHERE uid=?", (0 if cur else 1, uid))
        log(aid, "BAN", f"{uid} -> {0 if cur else 1}")
        try:
            await context.bot.send_message(uid, "✅ You have been unbanned." if cur else T[ulang(uid)]["banned"], parse_mode="HTML")
        except Exception:
            pass
        await adm_edit(q, *user_card(uid))
    elif cmd == "msg":
        ud["adm"] = {"mode": "msg_user", "target": int(arg)}
        await adm_edit(q, head("💬", "MESSAGE USER") + f"<code>{arg}</code> কে মেসেজ লিখুন:", InlineKeyboardMarkup([[B("✖️ Cancel", f"a:u:{arg}")]]))

    # ---- withdrawals
    elif cmd == "wd":
        await adm_edit(q, *wd_list())
    elif cmd == "wdh":
        rows = many("SELECT * FROM withdraws WHERE status!='pending' ORDER BY processed_at DESC LIMIT 12")
        kb = [[B(f"{'✅' if r['status']=='paid' else '❌'} {r['method']} • {fm(r['amount'])} • {r['uid']}", f"a:wdv:{r['pid']}")] for r in rows]
        kb.append([B("⬅️ Back", "a:wd"), *HOME])
        await adm_edit(q, head("📚", "WITHDRAW HISTORY") + "Latest 12", InlineKeyboardMarkup(kb))
    elif cmd == "wdv":
        await adm_edit(q, *wd_view(arg))
    elif cmd in ("wda", "wdr"):
        w = one("SELECT * FROM withdraws WHERE pid=?", (arg,))
        if not w or w["status"] != "pending":
            await q.answer("Already processed!", show_alert=True)
            await adm_edit(q, *wd_view(arg))
            return
        ok = cmd == "wda"
        xc("UPDATE withdraws SET status=?, processed_at=?, processed_by=? WHERE pid=? AND status='pending'",
           ("paid" if ok else "rejected", now_iso(), aid, arg))
        if not ok:
            add_balance_refund(w["uid"], w["amount"])
        log(aid, "WD_PAID" if ok else "WD_REJECT", arg)
        try:
            lang = ulang(w["uid"])
            await context.bot.send_message(w["uid"], T[lang]["wd_paid" if ok else "wd_rej"].format(
                method=esc(w["method"]), acct=esc(w["account"]), amount=fm(w["amount"]), pid=arg), parse_mode="HTML")
        except Exception:
            pass
        await adm_edit(q, *wd_view(arg))

    # ---- broadcast
    elif cmd == "bc":
        ud["adm"] = {"mode": "broadcast"}
        await adm_edit(q, head("📢", "BROADCAST") + "সবাইকে পাঠানোর মেসেজ লিখুন (Bold/Italic/Link সাপোর্টেড):", InlineKeyboardMarkup([HOME]))
    elif cmd == "bcgo":
        text = (ud.get("adm") or {}).get("text")
        ud.pop("adm", None)
        if not text:
            return
        asyncio.create_task(run_broadcast(context.bot, aid, text))
        await adm_edit(q, "🚀 <b>Broadcast started…</b>\nশেষে রিপোর্ট পাবেন।", InlineKeyboardMarkup([HOME]))

    # ---- settings
    elif cmd == "set":
        ud.pop("adm", None)
        await adm_edit(q, *settings_menu())
    elif cmd == "sg":
        await adm_edit(q, *settings_group(arg))
    elif cmd == "st":
        set_S(arg, not S(arg))
        log(aid, "SETTING", f"{arg}={S(arg)}")
        if arg == "force_join_on":
            await adm_edit(q, *channels_menu())
        else:
            await adm_edit(q, *settings_group(SETTINGS[arg][3]))
    elif cmd == "sk":
        d, label, typ, grp = SETTINGS[arg]
        ud["adm"] = {"mode": "set", "key": arg}
        cur = S(arg)
        shown = (cur[:3] + "***") if arg == "api_key" and cur else cur
        await adm_edit(q, head("✏️", label) + f"Current: <code>{esc(shown) or '—'}</code>\n\nনতুন মান পাঠান" +
                       (" (খালি করতে <code>-</code> পাঠান)" if typ == "text" else " (শুধু সংখ্যা)"),
                       InlineKeyboardMarkup([[B("✖️ Cancel", f"a:sg:{grp}")]]))

    # ---- services / methods / channels / admins
    elif cmd == "svcs":
        await adm_edit(q, *services_menu())
    elif cmd == "svt":
        xc("UPDATE services SET enabled=1-enabled WHERE sid=?", (arg,))
        await adm_edit(q, *services_menu())
    elif cmd == "svd":
        xc("DELETE FROM services WHERE sid=?", (arg,))
        await adm_edit(q, *services_menu())
    elif cmd == "sva":
        ud["adm"] = {"mode": "svc_add"}
        await adm_edit(q, head("🧩", "ADD SERVICE") + "সার্ভিসের নাম পাঠান (যেমন <code>TIKTOK</code>):", InlineKeyboardMarkup([[B("✖️ Cancel", "a:svcs")]]))
    elif cmd == "wm":
        await adm_edit(q, *methods_menu())
    elif cmd == "wmt":
        xc("UPDATE methods SET enabled=1-enabled WHERE name=?", (arg,))
        await adm_edit(q, *methods_menu())
    elif cmd == "wmd":
        xc("DELETE FROM methods WHERE name=?", (arg,))
        await adm_edit(q, *methods_menu())
    elif cmd == "wma":
        ud["adm"] = {"mode": "method_add"}
        await adm_edit(q, head("💳", "ADD METHOD") + "মেথডের নাম পাঠান (যেমন <code>UPAY</code>):", InlineKeyboardMarkup([[B("✖️ Cancel", "a:wm")]]))
    elif cmd == "ch":
        await adm_edit(q, *channels_menu())
    elif cmd == "chd":
        xc("DELETE FROM channels WHERE id=?", (int(arg),))
        await adm_edit(q, *channels_menu())
    elif cmd == "cha":
        ud["adm"] = {"mode": "ch_add"}
        await adm_edit(q, head("📌", "ADD CHANNEL") + "ফরম্যাট:\n<code>@channelusername</code>\nঅথবা\n<code>-1001234567890 https://t.me/+invitelink</code>",
                       InlineKeyboardMarkup([[B("✖️ Cancel", "a:ch")]]))
    elif cmd == "adm":
        await adm_edit(q, *admins_menu())
    elif cmd == "adma":
        if not is_owner(aid):
            await q.answer("Only owner can add admins", show_alert=True)
            return
        ud["adm"] = {"mode": "adm_add"}
        await adm_edit(q, head("🛡", "ADD ADMIN") + "নতুন অ্যাডমিনের numeric Telegram ID পাঠান:", InlineKeyboardMarkup([[B("✖️ Cancel", "a:adm")]]))
    elif cmd == "admd":
        if not is_owner(aid):
            await q.answer("Only owner", show_alert=True)
            return
        if not is_owner(int(arg)):
            xc("DELETE FROM admins WHERE uid=?", (int(arg),))
        await adm_edit(q, *admins_menu())

    # ---- misc
    elif cmd == "top":
        rows = many("SELECT uid,name,total_earned FROM users ORDER BY total_earned DESC LIMIT 10")
        medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
        lines = [f"{medals[i]} <code>{r['uid']}</code> {esc((r['name'] or '')[:14])} — <b>{fm(r['total_earned'])}</b>" for i, r in enumerate(rows)]
        await adm_edit(q, head("🏆", "TOP EARNERS") + ("\n".join(lines) or "—"), InlineKeyboardMarkup([HOME]))
    elif cmd == "logs":
        rows = many("SELECT * FROM logs ORDER BY id DESC LIMIT 15")
        icon = {"NUMBERS": "📱", "OTP": "🔑", "REFERRAL": "👥", "WITHDRAW": "💳", "BROADCAST": "📢"}
        lines = [f"{icon.get(r['action'], '🛡')} <code>{r['ts'][11:]}</code> {r['uid']} {r['action']}" for r in rows]
        await adm_edit(q, head("📜", "RECENT LOGS") + ("\n".join(lines) or "—"),
                       InlineKeyboardMarkup([[B("🔄 Refresh", "a:logs", style="success"), *HOME]]))
    elif cmd == "api":
        await refresh_services()
        names = ", ".join(str(s.get("sid")) for s in _live["services"]) or "—"
        await adm_edit(q, head("🔌", "API TEST") + f"Status: {'🟢 OK' if API_STATUS['ok'] else '🔴 FAIL'}\n"
                       f"Services: <code>{esc(names)}</code>\nError: <code>{esc(API_STATUS['err'] or '—')}</code>",
                       InlineKeyboardMarkup([[B("🔄 Retest", "a:api", style="success"), *HOME]]))
    elif cmd == "backup":
        tmp = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        try:
            with _lock:
                dst = sqlite3.connect(tmp)
                _db.backup(dst)
                dst.close()
            with open(tmp, "rb") as f:
                await context.bot.send_document(aid, f, filename=tmp, caption="📤 Database backup")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    elif cmd == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["uid", "name", "username", "lang", "balance", "total_earned", "banned", "joined", "referred_by", "ref_count"])
        for r in many("SELECT * FROM users"):
            w.writerow([r["uid"], r["name"], r["username"], r["lang"], r["balance"], r["total_earned"], r["banned"], r["joined"], r["referred_by"], r["ref_count"]])
        f = io.BytesIO(buf.getvalue().encode("utf-8"))
        f.name = "users.csv"
        await context.bot.send_document(aid, f, caption="📄 Users export")


def add_balance_refund(uid, amount):
    """Reject রিফান্ড — total_earned বাড়ায় না"""
    xc("UPDATE users SET balance=ROUND(balance+?,2) WHERE uid=?", (amount, int(uid)))


async def admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    aid = update.effective_user.id
    ud = context.user_data
    st = ud.get("adm") or {}
    mode = st.get("mode")
    text = update.message.text.strip()
    reply = update.message.reply_text
    home_kb = InlineKeyboardMarkup([HOME])

    if mode == "search":
        qv = text.lstrip("@")
        uid = int(qv) if qv.isdigit() else (one("SELECT uid FROM users WHERE LOWER(username)=LOWER(?)", (qv,)) or {}).get("uid")
        ud.pop("adm", None)
        if not uid or not one("SELECT 1 FROM users WHERE uid=?", (uid,)):
            await reply("❌ User not found.", reply_markup=home_kb)
            return
        t, kb = user_card(uid)
        await reply(t, parse_mode="HTML", reply_markup=kb)

    elif mode in ("bal_add", "bal_sub"):
        try:
            amt = float(text)
            assert amt > 0
        except Exception:
            await reply("⚠️ সঠিক সংখ্যা দিন।")
            return
        tgt = st["target"]
        new = add_balance(tgt, amt if mode == "bal_add" else -amt)
        log(aid, "BAL_ADJ", f"{tgt} {'+' if mode == 'bal_add' else '-'}{amt}")
        ud.pop("adm", None)
        try:
            await context.bot.send_message(tgt, f"💰 <b>Wallet updated by admin</b>\n{'+' if mode == 'bal_add' else '−'}{fm(amt)} BDT • New balance <code>{fm(new)} BDT</code>", parse_mode="HTML")
        except Exception:
            pass
        t, kb = user_card(tgt)
        await reply("✅ Done.\n\n" + t, parse_mode="HTML", reply_markup=kb)

    elif mode == "msg_user":
        tgt = st["target"]
        ud.pop("adm", None)
        try:
            await context.bot.send_message(tgt, f"📩 <b>Message from Admin</b>\n\n{update.message.text_html}", parse_mode="HTML")
            await reply("✅ Sent.", reply_markup=InlineKeyboardMarkup([[B("👤 User", f"a:u:{tgt}")], HOME]))
        except Exception as e:
            await reply(f"❌ Failed: {esc(e)}", parse_mode="HTML", reply_markup=home_kb)

    elif mode == "broadcast":
        html_text = update.message.text_html
        total = scalar("SELECT COUNT(*) FROM users WHERE banned=0")
        ud["adm"] = {"mode": "broadcast_confirm", "text": html_text}
        await reply(head("📢", f"PREVIEW → {total} users") + html_text, parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[B("🚀 Send now", "a:bcgo", style="success"), B("✖️ Cancel", "a:home", style="danger")]]))

    elif mode == "set":
        key = st["key"]
        d, label, typ, grp = SETTINGS[key]
        try:
            if typ == "text":
                val = "" if text == "-" else (update.message.text_html if key.startswith("welcome") else text)
            elif typ == "int":
                val = int(float(text))
            else:
                val = float(text)
            if typ in ("int", "float") and val < 0:
                raise ValueError
            if key == "numbers_per_req":
                val = max(1, min(6, val))
        except Exception:
            await reply("⚠️ সঠিক মান দিন।")
            return
        set_S(key, val)
        log(aid, "SETTING", f"{key} updated")
        ud.pop("adm", None)
        t, kb = settings_group(grp)
        await reply(f"✅ <b>{esc(label)}</b> updated.\n\n" + t, parse_mode="HTML", reply_markup=kb)

    elif mode == "svc_add":
        sid = re.sub(r"[^A-Za-z0-9_]", "", text).upper()
        ud.pop("adm", None)
        if sid:
            xc("INSERT OR IGNORE INTO services(sid,enabled) VALUES(?,1)", (sid,))
        t, kb = services_menu()
        await reply(t, parse_mode="HTML", reply_markup=kb)

    elif mode == "method_add":
        name = re.sub(r"[^A-Za-z0-9_ ]", "", text).upper().strip()[:20]
        ud.pop("adm", None)
        if name:
            xc("INSERT OR IGNORE INTO methods(name,enabled) VALUES(?,1)", (name,))
        t, kb = methods_menu()
        await reply(t, parse_mode="HTML", reply_markup=kb)

    elif mode == "ch_add":
        parts = text.split()
        chat = parts[0]
        link = parts[1] if len(parts) > 1 else (f"https://t.me/{chat.lstrip('@')}" if chat.startswith("@") else "")
        ud.pop("adm", None)
        xc("INSERT INTO channels(chat,link) VALUES(?,?)", (chat, link))
        t, kb = channels_menu()
        await reply("✅ Added.\n\n" + t, parse_mode="HTML", reply_markup=kb)

    elif mode == "adm_add":
        ud.pop("adm", None)
        if text.isdigit():
            xc("INSERT OR IGNORE INTO admins(uid,added) VALUES(?,?)", (int(text), now_iso()))
        t, kb = admins_menu()
        await reply(t, parse_mode="HTML", reply_markup=kb)


# =====================================================================
# 🚀 BOOT
# =====================================================================
async def error_handler(update, context):
    print(f"⚠️ Handler error: {type(context.error).__name__}: {context.error}")


async def post_init(app):
    me = await app.bot.get_me()
    if not S("panel_link"):
        set_S("panel_link", f"https://t.me/{me.username}")
    asyncio.create_task(services_loop())
    asyncio.create_task(monitor_loop(app))
    print(f"🚀 @{me.username} running — admin: /admin")


def main():
    if BOT_TOKEN.startswith("PUT_"):
        raise SystemExit("❌ BOT_TOKEN সেট করুন (ফাইলের উপরে CONFIG অংশে)")
    app = ApplicationBuilder().token(BOT_TOKEN).concurrent_updates(True).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("language", language_cmd))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
