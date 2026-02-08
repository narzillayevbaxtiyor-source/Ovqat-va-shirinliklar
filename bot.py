import os
import sqlite3
import logging
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("food_bot")

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
ADMIN_ID = int((os.getenv("ADMIN_ID") or "0").strip() or "0")
DB_PATH = os.getenv("DB_PATH", "bot.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env yo'q")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID env yo'q (sizning Telegram ID)")

# ====== DB ======
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,              -- "food" or "dessert"
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        price REAL DEFAULT 0,
        min_qty INTEGER DEFAULT 1,
        max_qty INTEGER DEFAULT 10,
        photo1_file_id TEXT NOT NULL,
        photo2_file_id TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        full_name TEXT,
        item_id INTEGER NOT NULL,
        qty INTEGER NOT NULL,
        delivery_type TEXT NOT NULL,         -- "location" / "address"
        address_text TEXT,
        latitude REAL,
        longitude REAL,
        contact_type TEXT,                   -- "phone" / "username"
        phone TEXT,
        tg_username TEXT,
        schedule_type TEXT NOT NULL,         -- "now" / "scheduled"
        scheduled_time_text TEXT,
        status TEXT NOT NULL,                -- new/accepted/canceled/preparing/onway/delivered
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)
    con.commit()
    con.close()

# ====== Helpers ======
def is_admin(u: Update) -> bool:
    return bool(u.effective_user and u.effective_user.id == ADMIN_ID)

def cat_label(cat: str) -> str:
    return "🍲 Ovqat" if cat == "food" else "🍰 Shirinliklar"

def status_label(st: str) -> str:
    return {
        "new": "🆕 Yangi",
        "accepted": "✅ Qabul qilindi",
        "canceled": "❌ Bekor qilindi",
        "preparing": "👨‍🍳 Tayyorlanyapti",
        "onway": "🚗 Yo‘lda",
        "delivered": "📦 Yetkazildi",
    }.get(st, st)

def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")

# ====== States ======
(
    ADMIN_MENU,
    ADMIN_ADD_TITLE,
    ADMIN_ADD_DESC,
    ADMIN_ADD_PRICE,
    ADMIN_ADD_MINMAX,
    ADMIN_ADD_PHOTO1,
    ADMIN_ADD_PHOTO2,
    CUSTOMER_BROWSE,
    CUSTOMER_PICK_QTY,
    CUSTOMER_DELIVERY,
    CUSTOMER_ADDRESS_TEXT,
    CUSTOMER_CONTACT,
    CUSTOMER_PHONE,
    CUSTOMER_SCHEDULE,
    CUSTOMER_SCHEDULE_TIME,
) = range(15)

# ====== Keyboards ======
def kb_admin_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🍲 Ovqatlar", callback_data="admin:cat:food"),
         InlineKeyboardButton("🍰 Shirinliklar", callback_data="admin:cat:dessert")],
        [InlineKeyboardButton("➕ Mahsulot qo‘shish", callback_data="admin:add:start")],
        [InlineKeyboardButton("📦 Buyurtmalar", callback_data="admin:orders")],
    ])

def kb_categories():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🍲 Ovqatlar", callback_data="cust:cat:food"),
         InlineKeyboardButton("🍰 Shirinliklar", callback_data="cust:cat:dessert")],
    ])

def kb_order_status(order_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Qabul qilindi", callback_data=f"admin:st:{order_id}:accepted"),
            InlineKeyboardButton("❌ Bekor qilindi", callback_data=f"admin:st:{order_id}:canceled"),
        ],
        [
            InlineKeyboardButton("👨‍🍳 Tayyorlanyapti", callback_data=f"admin:st:{order_id}:preparing"),
            InlineKeyboardButton("🚗 Yo‘lda", callback_data=f"admin:st:{order_id}:onway"),
        ],
        [InlineKeyboardButton("📦 Yetkazildi", callback_data=f"admin:st:{order_id}:delivered")],
    ])

def kb_qty(min_q, max_q, current):
    btns = []
    row = []
    for q in range(min_q, max_q + 1):
        txt = f"✅ {q}" if q == current else str(q)
        row.append(InlineKeyboardButton(txt, callback_data=f"cust:qty:{q}"))
        if len(row) == 5:
            btns.append(row)
            row = []
    if row:
        btns.append(row)
    btns.append([InlineKeyboardButton("➡️ Keyingi", callback_data="cust:qty:next")])
    return InlineKeyboardMarkup(btns)

# ====== /start ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    if is_admin(update):
        await update.message.reply_text(
            "👋 Admin panel",
            reply_markup=kb_admin_main()
        )
        return ADMIN_MENU

    await update.message.reply_text(
        "Assalomu alaykum! Buyurtma berish uchun bo‘lim tanlang:",
        reply_markup=kb_categories()
    )
    return CUSTOMER_BROWSE

# ====== ADMIN FLOW: add item ======
async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["add_item"] = {
        "category": None,
        "title": None,
        "description": "",
        "price": 0.0,
        "min_qty": 1,
        "max_qty": 10,
        "photo1": None,
        "photo2": None,
    }
    await q.message.reply_text("Mahsulot kategoriyasini tanlang:", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🍲 Ovqat", callback_data="admin:add:cat:food"),
         InlineKeyboardButton("🍰 Shirinlik", callback_data="admin:add:cat:dessert")]
    ]))
    return ADMIN_ADD_TITLE

async def admin_add_pick_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    cat = q.data.split(":")[-1]
    context.user_data["add_item"]["category"] = cat
    await q.message.reply_text("Mahsulot nomini yozing (masalan: 'Palov'):")
    return ADMIN_ADD_TITLE

async def admin_add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["add_item"]["title"] = update.message.text.strip()
    await update.message.reply_text("Qisqa ta’rif yozing (ixtiyoriy). Bo‘sh qoldirish uchun `-` yozing:", parse_mode=ParseMode.MARKDOWN)
    return ADMIN_ADD_DESC

async def admin_add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    context.user_data["add_item"]["description"] = "" if txt == "-" else txt
    await update.message.reply_text("Narxini yozing (son). Masalan: 25 yoki 25.5")
    return ADMIN_ADD_PRICE

async def admin_add_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text.strip())
        if price < 0:
            raise ValueError()
    except Exception:
        await update.message.reply_text("❌ Narx noto‘g‘ri. Qayta yozing: 25 yoki 25.5")
        return ADMIN_ADD_PRICE

    context.user_data["add_item"]["price"] = price
    await update.message.reply_text("Min va Max buyurtma sonini yozing. Format: `min max` (masalan: `1 10`)", parse_mode=ParseMode.MARKDOWN)
    return ADMIN_ADD_MINMAX

async def admin_add_minmax(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.strip().split()
        mn = int(parts[0]); mx = int(parts[1])
        if mn < 1 or mx < mn:
            raise ValueError()
    except Exception:
        await update.message.reply_text("❌ Format noto‘g‘ri. Masalan: `1 10`", parse_mode=ParseMode.MARKDOWN)
        return ADMIN_ADD_MINMAX

    context.user_data["add_item"]["min_qty"] = mn
    context.user_data["add_item"]["max_qty"] = mx
    await update.message.reply_text("1-rasmni yuboring (galereyadan rasm):")
    return ADMIN_ADD_PHOTO1

async def admin_add_photo1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ Rasm yuboring (photo).")
        return ADMIN_ADD_PHOTO1
    file_id = update.message.photo[-1].file_id
    context.user_data["add_item"]["photo1"] = file_id
    await update.message.reply_text("2-rasmni yuboring (galereyadan rasm):")
    return ADMIN_ADD_PHOTO2

async def admin_add_photo2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ Rasm yuboring (photo).")
        return ADMIN_ADD_PHOTO2
    file_id = update.message.photo[-1].file_id
    context.user_data["add_item"]["photo2"] = file_id

    it = context.user_data["add_item"]
    con = db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO items(category,title,description,price,min_qty,max_qty,photo1_file_id,photo2_file_id,is_active,created_at)
        VALUES(?,?,?,?,?,?,?,?,1,?)
    """, (
        it["category"], it["title"], it["description"], it["price"],
        it["min_qty"], it["max_qty"], it["photo1"], it["photo2"], now_iso()
    ))
    con.commit()
    con.close()

    await update.message.reply_text("✅ Saqlandi. Admin panelga qaytdingiz.", reply_markup=kb_admin_main())
    return ADMIN_MENU

# ====== ADMIN: list items by category ======
async def admin_show_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    cat = q.data.split(":")[-1]
    con = db()
    cur = con.cursor()
    cur.execute("SELECT * FROM items WHERE category=? ORDER BY id DESC LIMIT 20", (cat,))
    rows = cur.fetchall()
    con.close()

    if not rows:
        await q.message.reply_text(f"{cat_label(cat)}: hozircha mahsulot yo‘q.")
        return ADMIN_MENU

    for r in rows:
        txt = (
            f"#{r['id']} — *{r['title']}*\n"
            f"{r['description']}\n"
            f"💰 Narx: *{r['price']}*\n"
            f"🔢 Min/Max: *{r['min_qty']}–{r['max_qty']}*\n"
            f"🟢 Aktiv: {'Ha' if r['is_active'] else 'Yo‘q'}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢/🔴 Aktivni almashtirish", callback_data=f"admin:toggle:{r['id']}")]
        ])
        await q.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    return ADMIN_MENU

async def admin_toggle_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    item_id = int(q.data.split(":")[-1])
    con = db(); cur = con.cursor()
    cur.execute("SELECT is_active FROM items WHERE id=?", (item_id,))
    row = cur.fetchone()
    if not row:
        con.close()
        await q.message.reply_text("❌ Topilmadi.")
        return ADMIN_MENU
    newv = 0 if row["is_active"] else 1
    cur.execute("UPDATE items SET is_active=? WHERE id=?", (newv, item_id))
    con.commit(); con.close()
    await q.message.reply_text(f"✅ Item #{item_id} aktivligi o‘zgardi.")
    return ADMIN_MENU

# ====== CUSTOMER FLOW ======
async def cust_pick_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    cat = q.data.split(":")[-1]
    con = db(); cur = con.cursor()
    cur.execute("SELECT * FROM items WHERE category=? AND is_active=1 ORDER BY id DESC", (cat,))
    items = cur.fetchall(); con.close()

    if not items:
        await q.message.reply_text("Hozircha bu bo‘limda mahsulot yo‘q.")
        return CUSTOMER_BROWSE

    # list items with buttons
    for it in items[:25]:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Buyurtma", callback_data=f"cust:item:{it['id']}")]
        ])
        await q.message.reply_text(
            f"*{it['title']}*\n{it['description']}\n💰 Narx: *{it['price']}*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb
        )
    return CUSTOMER_BROWSE

async def cust_open_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    item_id = int(q.data.split(":")[-1])

    con = db(); cur = con.cursor()
    cur.execute("SELECT * FROM items WHERE id=? AND is_active=1", (item_id,))
    it = cur.fetchone()
    con.close()
    if not it:
        await q.message.reply_text("❌ Mahsulot topilmadi yoki aktiv emas.")
        return CUSTOMER_BROWSE

    context.user_data["order"] = {
        "item_id": item_id,
        "qty": it["min_qty"],
        "min_qty": it["min_qty"],
        "max_qty": it["max_qty"],
        "delivery_type": None,
        "address_text": None,
        "lat": None,
        "lng": None,
        "contact_type": None,
        "phone": None,
        "tg_username": None,
        "schedule_type": None,
        "scheduled_time_text": None,
    }

    # send 2 photos as album (media group)
    await context.bot.send_media_group(
        chat_id=q.message.chat_id,
        media=[
            # caption only on first
            __import__("telegram").InputMediaPhoto(
                it["photo1_file_id"],
                caption=(
                    f"*{it['title']}*\n{it['description']}\n"
                    f"💰 Narx: *{it['price']}*\n"
                    f"🔢 Min/Max: *{it['min_qty']}–{it['max_qty']}*"
                ),
                parse_mode=ParseMode.MARKDOWN,
            ),
            __import__("telegram").InputMediaPhoto(it["photo2_file_id"]),
        ],
    )

    await q.message.reply_text(
        "Buyurtma sonini tanlang:",
        reply_markup=kb_qty(it["min_qty"], it["max_qty"], it["min_qty"])
    )
    return CUSTOMER_PICK_QTY

async def cust_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data.split(":")[-1]
    od = context.user_data.get("order")
    if not od:
        await q.message.reply_text("Buyurtma sessiyasi topilmadi. /start qiling.")
        return CUSTOMER_BROWSE

    if data == "next":
        # go to delivery
        kb = ReplyKeyboardMarkup(
            [
                [KeyboardButton("📍 Lokatsiya yuborish", request_location=True)],
                ["✍️ Manzilni yozaman"],
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await q.message.reply_text("Yetkazib berish uchun lokatsiya yuboring yoki manzilni qo‘lda yozing:", reply_markup=kb)
        return CUSTOMER_DELIVERY

    try:
        chosen = int(data)
    except:
        return CUSTOMER_PICK_QTY

    if chosen < od["min_qty"] or chosen > od["max_qty"]:
        await q.message.reply_text("❌ Ruxsat etilgan oraliqdan tashqarida.")
        return CUSTOMER_PICK_QTY

    od["qty"] = chosen
    await q.message.edit_reply_markup(reply_markup=kb_qty(od["min_qty"], od["max_qty"], chosen))
    return CUSTOMER_PICK_QTY

async def cust_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    od = context.user_data.get("order")
    if not od:
        await update.message.reply_text("Buyurtma sessiyasi topilmadi. /start qiling.")
        return CUSTOMER_BROWSE

    if update.message.location:
        od["delivery_type"] = "location"
        od["lat"] = update.message.location.latitude
        od["lng"] = update.message.location.longitude
        # contact
        kb = ReplyKeyboardMarkup(
            [
                [KeyboardButton("📞 Telefon raqam yuborish", request_contact=True)],
                ["👤 Telegram nik qoldiraman"],
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await update.message.reply_text("Aloqa uchun telefon yuboring yoki telegram nik qoldiring:", reply_markup=kb)
        return CUSTOMER_CONTACT

    txt = (update.message.text or "").strip()
    if txt == "✍️ Manzilni yozaman":
        await update.message.reply_text("Manzilni yozing (mahalla/ko‘cha/uy raqami):")
        return CUSTOMER_ADDRESS_TEXT

    # if user typed address directly
    if txt:
        od["delivery_type"] = "address"
        od["address_text"] = txt
        kb = ReplyKeyboardMarkup(
            [
                [KeyboardButton("📞 Telefon raqam yuborish", request_contact=True)],
                ["👤 Telegram nik qoldiraman"],
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await update.message.reply_text("Aloqa uchun telefon yuboring yoki telegram nik qoldiring:", reply_markup=kb)
        return CUSTOMER_CONTACT

    await update.message.reply_text("Lokatsiya yuboring yoki manzilni yozing.")
    return CUSTOMER_DELIVERY

async def cust_address_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    od = context.user_data.get("order")
    if not od:
        await update.message.reply_text("Buyurtma sessiyasi topilmadi. /start qiling.")
        return CUSTOMER_BROWSE
    od["delivery_type"] = "address"
    od["address_text"] = update.message.text.strip()

    kb = ReplyKeyboardMarkup(
        [
            [KeyboardButton("📞 Telefon raqam yuborish", request_contact=True)],
            ["👤 Telegram nik qoldiraman"],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text("Aloqa uchun telefon yuboring yoki telegram nik qoldiring:", reply_markup=kb)
    return CUSTOMER_CONTACT

async def cust_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    od = context.user_data.get("order")
    if not od:
        await update.message.reply_text("Buyurtma sessiyasi topilmadi. /start qiling.")
        return CUSTOMER_BROWSE

    if update.message.contact and update.message.contact.phone_number:
        od["contact_type"] = "phone"
        od["phone"] = update.message.contact.phone_number
    else:
        # user wants username
        if (update.message.text or "").strip() == "👤 Telegram nik qoldiraman":
            await update.message.reply_text("Telegram nikingizni yozing (masalan: @username):")
            return CUSTOMER_PHONE
        # user typed username already
        txt = (update.message.text or "").strip()
        if txt.startswith("@"):
            od["contact_type"] = "username"
            od["tg_username"] = txt
        else:
            await update.message.reply_text("Telefon yuboring yoki @username yozing.")
            return CUSTOMER_CONTACT

    # schedule
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Hozir", callback_data="cust:sched:now"),
         InlineKeyboardButton("🕒 Belgilangan vaqtda", callback_data="cust:sched:scheduled")]
    ])
    await update.message.reply_text("Buyurtma vaqtini tanlang:", reply_markup=kb)
    return CUSTOMER_SCHEDULE

async def cust_username_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    od = context.user_data.get("order")
    txt = (update.message.text or "").strip()
    if not txt.startswith("@"):
        await update.message.reply_text("Iltimos @username formatida yozing. Masalan: @Saudia0dan")
        return CUSTOMER_PHONE
    od["contact_type"] = "username"
    od["tg_username"] = txt

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Hozir", callback_data="cust:sched:now"),
         InlineKeyboardButton("🕒 Belgilangan vaqtda", callback_data="cust:sched:scheduled")]
    ])
    await update.message.reply_text("Buyurtma vaqtini tanlang:", reply_markup=kb)
    return CUSTOMER_SCHEDULE

async def cust_schedule_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    od = context.user_data.get("order")
    if not od:
        await q.message.reply_text("Buyurtma sessiyasi topilmadi. /start qiling.")
        return CUSTOMER_BROWSE

    kind = q.data.split(":")[-1]
    od["schedule_type"] = kind

    if kind == "now":
        od["scheduled_time_text"] = None
        return await finalize_order(q.message, context)

    await q.message.reply_text("Vaqtni yozing (masalan: `18:30` yoki `Bugun 20:00`):", parse_mode=ParseMode.MARKDOWN)
    return CUSTOMER_SCHEDULE_TIME

async def cust_schedule_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    od = context.user_data.get("order")
    if not od:
        await update.message.reply_text("Buyurtma sessiyasi topilmadi. /start qiling.")
        return CUSTOMER_BROWSE
    od["scheduled_time_text"] = update.message.text.strip()
    # finalize
    return await finalize_order(update.message, context)

async def finalize_order(message, context: ContextTypes.DEFAULT_TYPE):
    od = context.user_data.get("order")
    if not od:
        await message.reply_text("Buyurtma sessiyasi topilmadi. /start qiling.")
        return CUSTOMER_BROWSE

    user = message.chat if hasattr(message, "chat") else None
    u = message.from_user

    con = db(); cur = con.cursor()
    cur.execute("SELECT * FROM items WHERE id=?", (od["item_id"],))
    it = cur.fetchone()
    if not it:
        con.close()
        await message.reply_text("❌ Mahsulot topilmadi.")
        return CUSTOMER_BROWSE

    created = now_iso()
    cur.execute("""
        INSERT INTO orders(user_id,username,full_name,item_id,qty,delivery_type,address_text,latitude,longitude,
                           contact_type,phone,tg_username,schedule_type,scheduled_time_text,status,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        u.id,
        u.username,
        f"{u.first_name or ''} {u.last_name or ''}".strip(),
        od["item_id"],
        od["qty"],
        od["delivery_type"],
        od["address_text"],
        od["lat"],
        od["lng"],
        od["contact_type"],
        od["phone"],
        od["tg_username"],
        od["schedule_type"],
        od["scheduled_time_text"],
        "new",
        created,
        created
    ))
    order_id = cur.lastrowid
    con.commit(); con.close()

    # notify admin
    lines = [
        f"📦 *Yangi buyurtma*  #{order_id}",
        f"👤 Mijoz: *{u.first_name}* (@{u.username})" if u.username else f"👤 Mijoz: *{u.first_name}*",
        f"🍽 Mahsulot: *{it['title']}* (#{it['id']})",
        f"🔢 Soni: *{od['qty']}*",
        f"💰 Narx: *{it['price']}*",
        f"⏱ Vaqt: *{'Hozir' if od['schedule_type']=='now' else od['scheduled_time_text']}*",
    ]
    if od["delivery_type"] == "location":
        lines.append("📍 Yetkazish: *Lokatsiya*")
    else:
        lines.append(f"📍 Manzil: *{od['address_text']}*")

    if od["contact_type"] == "phone":
        lines.append(f"📞 Tel: *{od['phone']}*")
    else:
        lines.append(f"👤 Nik: *{od['tg_username']}*")

    admin_text = "\n".join(lines)
    await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_order_status(order_id))

    if od["delivery_type"] == "location":
        await context.bot.send_location(chat_id=ADMIN_ID, latitude=od["lat"], longitude=od["lng"])

    # confirm to customer
    await message.reply_text(
        f"✅ Buyurtmangiz qabul qilindi (ID: {order_id}). Holat o‘zgarishi admin tomonidan yuboriladi.",
        reply_markup=ReplyKeyboardMarkup([["🏠 Bosh menu"]], resize_keyboard=True)
    )

    context.user_data.pop("order", None)

    # show categories again
    await message.reply_text("Yana buyurtma berish uchun bo‘lim tanlang:", reply_markup=kb_categories())
    return CUSTOMER_BROWSE

# ====== ADMIN: orders list + status update ======
async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    con = db(); cur = con.cursor()
    cur.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 20")
    rows = cur.fetchall(); con.close()

    if not rows:
        await q.message.reply_text("Buyurtmalar yo‘q.")
        return ADMIN_MENU

    for r in rows:
        await q.message.reply_text(
            f"#{r['id']} — {status_label(r['status'])}\n"
            f"User: {r['full_name']} (@{r['username']})\n"
            f"Item #{r['item_id']} | qty {r['qty']}\n"
            f"Time: {'Hozir' if r['schedule_type']=='now' else r['scheduled_time_text']}",
            reply_markup=kb_order_status(r["id"])
        )
    return ADMIN_MENU

async def admin_set_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, _, oid, st = q.data.split(":")
    order_id = int(oid)

    con = db(); cur = con.cursor()
    cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
    r = cur.fetchone()
    if not r:
        con.close()
        await q.message.reply_text("❌ Buyurtma topilmadi.")
        return ADMIN_MENU

    cur.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?", (st, now_iso(), order_id))
    con.commit(); con.close()

    # notify customer
    try:
        await context.bot.send_message(
            chat_id=r["user_id"],
            text=f"📦 Buyurtma #{order_id} holati yangilandi: *{status_label(st)}*",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        log.warning("Customer notify failed: %s", e)

    await q.message.reply_text(f"✅ Buyurtma #{order_id} holati: {status_label(st)}")
    return ADMIN_MENU

# ====== Router for callbacks ======
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    # admin
    if data == "admin:add:start":
        return await admin_add_start(update, context)
    if data.startswith("admin:add:cat:"):
        return await admin_add_pick_cat(update, context)
    if data.startswith("admin:cat:"):
        return await admin_show_cat(update, context)
    if data.startswith("admin:toggle:"):
        return await admin_toggle_item(update, context)
    if data == "admin:orders":
        return await admin_orders(update, context)
    if data.startswith("admin:st:"):
        return await admin_set_status(update, context)

    # customer
    if data.startswith("cust:cat:"):
        return await cust_pick_cat(update, context)
    if data.startswith("cust:item:"):
        return await cust_open_item(update, context)
    if data.startswith("cust:qty:"):
        return await cust_qty(update, context)
    if data.startswith("cust:sched:"):
        return await cust_schedule_pick(update, context)

    await q.answer()
    return ConversationHandler.END

# ====== Main ======
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ADMIN_MENU: [CallbackQueryHandler(callback_router)],
            ADMIN_ADD_TITLE: [
                CallbackQueryHandler(callback_router),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_title),
            ],
            ADMIN_ADD_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_desc)],
            ADMIN_ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_price)],
            ADMIN_ADD_MINMAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_minmax)],
            ADMIN_ADD_PHOTO1: [MessageHandler(filters.PHOTO, admin_add_photo1)],
            ADMIN_ADD_PHOTO2: [MessageHandler(filters.PHOTO, admin_add_photo2)],

            CUSTOMER_BROWSE: [
                CallbackQueryHandler(callback_router),
                MessageHandler(filters.Regex("^🏠 Bosh menu$"), start),
            ],
            CUSTOMER_PICK_QTY: [CallbackQueryHandler(callback_router)],
            CUSTOMER_DELIVERY: [MessageHandler(filters.LOCATION | (filters.TEXT & ~filters.COMMAND), cust_delivery)],
            CUSTOMER_ADDRESS_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_address_text)],
            CUSTOMER_CONTACT: [MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), cust_contact)],
            CUSTOMER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_username_text)],
            CUSTOMER_SCHEDULE: [CallbackQueryHandler(callback_router)],
            CUSTOMER_SCHEDULE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_schedule_time)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
