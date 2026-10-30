from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, make_response, send_file, abort
import sqlite3
import json
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
import uuid
import mimetypes
import io
from datetime import datetime, date
from functools import wraps

app = Flask(__name__)
app.secret_key = "change-this-secret-key"
app.config["MAX_CONTENT_LENGTH"] = 11 * 1024 * 1024  # audio plus multipart form overhead
app.config["MAX_CONTENT_LENGTH"] = 11 * 1024 * 1024  # audio plus multipart form overhead

BASE_DIR = Path(__file__).resolve().parent
DB = "procurement.db"
VOICE_UPLOAD_DIR = BASE_DIR / "private_uploads" / "voice_messages"
VOICE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_VOICE_BYTES = 10 * 1024 * 1024  # 10 MB per recording
ALLOWED_VOICE_EXTENSIONS = {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".mp4"}
TRANSLATIONS_DIR = Path(__file__).resolve().parent / "translations"
SUPPORTED_LANGUAGES = {
    "en": "English",
    "kn": "ಕನ್ನಡ",
    "hi": "हिन्दी",
    "mr": "मराठी",
    "te": "తెలుగు",
    "ta": "தமிழ்",
}
DEFAULT_LANGUAGE = "en"

# Change these credentials before real deployment.
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "Admin@123"


def load_translations():
    data = {}
    for lang in SUPPORTED_LANGUAGES:
        path = TRANSLATIONS_DIR / f"{lang}.json"
        try:
            data[lang] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data[lang] = {}
    return data

TRANSLATIONS = load_translations()


def current_language():
    lang = session.get("language") or request.cookies.get("kisansetu_language") or DEFAULT_LANGUAGE
    return lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def t(key, **kwargs):
    lang = current_language()
    value = TRANSLATIONS.get(lang, {}).get(key, TRANSLATIONS.get(DEFAULT_LANGUAGE, {}).get(key, key))
    try:
        return value.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return value


def safe_key(value):
    return str(value).strip().lower().replace(" ", "_").replace("/", "_")


def crop_label(value):
    key = f"crop_{safe_key(value)}"
    return t(key) if key in TRANSLATIONS.get(DEFAULT_LANGUAGE, {}) else value


def status_label(value):
    key = f"status_{safe_key(value)}"
    return t(key) if key in TRANSLATIONS.get(DEFAULT_LANGUAGE, {}) else value


def notification_label(message):
    """Translate known system-generated notification messages without changing stored DB data."""
    text = str(message)
    if text.startswith("Slot confirmed: "):
        try:
            rest = text[len("Slot confirmed: "):]
            when, token_part = rest.split(". Token: ", 1)
            return t("notification_slot_confirmed", when=when, token=token_part)
        except ValueError:
            return text
    if text.startswith("Procurement updated: "):
        try:
            rest = text[len("Procurement updated: "):]
            procurement_status, payment_part = rest.split(". Payment: ", 1)
            payment_status, amount = payment_part.split(". Amount: ₹", 1)
            return t("notification_procurement_updated", procurement=status_label(procurement_status), payment=status_label(payment_status), amount=amount)
        except ValueError:
            return text
    if text.startswith("Queue/slot update: Token ") and " is now " in text:
        token, status = text[len("Queue/slot update: Token "):].split(" is now ", 1)
        return t("notification_queue_update", token=token, status=status_label(status))
    return text


def flash_key(key, category="message", **kwargs):
    flash(t(key, **kwargs), category)


@app.context_processor
def inject_i18n():
    return {
        "t": t,
        "current_language": current_language(),
        "supported_languages": SUPPORTED_LANGUAGES,
        "translations_for_js": TRANSLATIONS.get(current_language(), TRANSLATIONS[DEFAULT_LANGUAGE]),
        "translations_en_for_js": TRANSLATIONS.get(DEFAULT_LANGUAGE, {}),
        "crop_label": crop_label,
        "status_label": status_label,
        "notification_label": notification_label,
    }


@app.before_request
def restore_language():
    lang = request.args.get("lang")
    if lang in SUPPORTED_LANGUAGES:
        session["language"] = lang


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS farmers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT UNIQUE NOT NULL,
        village TEXT NOT NULL,
        crop TEXT NOT NULL,
        password TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS slots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        farmer_id INTEGER NOT NULL,
        slot_date TEXT NOT NULL,
        slot_time TEXT NOT NULL,
        token TEXT NOT NULL,
        status TEXT DEFAULT 'Booked',
        created_at TEXT NOT NULL,
        FOREIGN KEY(farmer_id) REFERENCES farmers(id)
    );

    CREATE TABLE IF NOT EXISTS procurement (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        farmer_id INTEGER NOT NULL,
        crop TEXT NOT NULL,
        quantity REAL NOT NULL,
        rate REAL NOT NULL,
        total REAL NOT NULL,
        procurement_status TEXT DEFAULT 'Pending',
        payment_status TEXT DEFAULT 'Pending',
        updated_at TEXT NOT NULL,
        FOREIGN KEY(farmer_id) REFERENCES farmers(id)
    );

    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        farmer_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL,
        is_read INTEGER DEFAULT 0,
        FOREIGN KEY(farmer_id) REFERENCES farmers(id)
    );

    CREATE TABLE IF NOT EXISTS voice_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        farmer_id INTEGER NOT NULL,
        sender_type TEXT NOT NULL CHECK(sender_type IN ('farmer', 'admin')),
        parent_id INTEGER,
        audio_filename TEXT NOT NULL,
        language TEXT NOT NULL DEFAULT 'en',
        status TEXT NOT NULL DEFAULT 'New',
        created_at TEXT NOT NULL,
        FOREIGN KEY(farmer_id) REFERENCES farmers(id),
        FOREIGN KEY(parent_id) REFERENCES voice_messages(id)
    );
 

    CREATE TABLE IF NOT EXISTS crop_prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        crop TEXT NOT NULL UNIQUE,
        msp_per_quintal REAL NOT NULL DEFAULT 0,
        market_price_per_quintal REAL NOT NULL DEFAULT 0,
        season TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS price_bargains (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        farmer_id INTEGER NOT NULL,
        crop TEXT NOT NULL,
        quantity_quintals REAL NOT NULL DEFAULT 1,
        farmer_offer REAL NOT NULL,
        admin_offer REAL,
        status TEXT NOT NULL DEFAULT 'Pending',
        farmer_message TEXT NOT NULL DEFAULT '',
        admin_message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(farmer_id) REFERENCES farmers(id)
    );
    """)
    # Add editable illustrative starter prices only when the table is empty.
    starter_crops = [
        ("Rice", 2300, 2500), ("Wheat", 2425, 2800), ("Maize", 2400, 2200),
        ("Jowar", 3699, 3000), ("Bajra", 2775, 2400), ("Ragi", 4886, 3500),
        ("Barley", 1980, 2200), ("Gram", 5650, 5800), ("Tur Dal", 8000, 9000),
        ("Moong", 8768, 8500), ("Urad", 7800, 7600), ("Lentil", 6700, 6500),
        ("Groundnut", 7263, 6000), ("Soybean", 5328, 4500), ("Sunflower", 7721, 6500),
        ("Sesame", 9846, 9000), ("Mustard", 6200, 6000), ("Cotton", 7710, 7500),
        ("Sugarcane", 355, 380), ("Jute", 5650, 5200), ("Safflower", 5940, 5600),
        ("Niger Seed", 9150, 8500), ("Copra", 11582, 12000), ("Potato", 1500, 1800),
        ("Onion", 2000, 2500), ("Tomato", 1800, 2200), ("Chilli", 7000, 8000),
        ("Turmeric", 12000, 14000), ("Garlic", 8000, 10000), ("Green Gram", 8768, 8500),
    ]
    count = conn.execute("SELECT COUNT(*) FROM crop_prices").fetchone()[0]
    if count == 0:
        now = datetime.now().isoformat()
        conn.executemany("INSERT INTO crop_prices(crop,msp_per_quintal,market_price_per_quintal,season,updated_at) VALUES(?,?,?,?,?)",
                         [(crop, msp, market, "Illustrative starter price — verify current rates", now) for crop, msp, market in starter_crops])
    conn.commit()
    conn.close()


def farmer_required():
    return "farmer_id" in session


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/set-language/<lang>")
def set_language(lang):
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE
    session["language"] = lang
    next_url = request.args.get("next") or request.referrer or url_for("home")
    response = make_response(redirect(next_url))
    response.set_cookie("kisansetu_language", lang, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return response


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        phone = request.form["phone"].strip()
        village = request.form["village"].strip()
        crop = request.form["crop"].strip()
        password = request.form["password"]

        if not all([name, phone, village, crop, password]):
            flash_key("flash_fill_all", "danger")
            return redirect(url_for("register"))

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO farmers(name, phone, village, crop, password, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (name, phone, village, crop, generate_password_hash(password), datetime.now().isoformat())
            )
            conn.commit()
            flash_key("flash_registration_success", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash_key("flash_phone_exists", "danger")
        finally:
            conn.close()

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone = request.form["phone"].strip()
        password = request.form["password"]

        conn = get_db()
        farmer = conn.execute("SELECT * FROM farmers WHERE phone=?", (phone,)).fetchone()
        conn.close()

        if farmer and check_password_hash(farmer["password"], password):
            selected_language = current_language()
            session.clear()
            session["language"] = selected_language
            session["farmer_id"] = farmer["id"]
            session["farmer_name"] = farmer["name"]
            return redirect(url_for("dashboard"))

        flash_key("flash_invalid_farmer_login", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    selected_language = current_language()
    session.clear()
    session["language"] = selected_language
    return redirect(url_for("home"))


@app.route("/dashboard")
def dashboard():
    if not farmer_required():
        return redirect(url_for("login"))

    farmer_id = session["farmer_id"]
    conn = get_db()
    farmer = conn.execute("SELECT * FROM farmers WHERE id=?", (farmer_id,)).fetchone()
    slots = conn.execute(
        "SELECT * FROM slots WHERE farmer_id=? ORDER BY slot_date DESC, slot_time DESC",
        (farmer_id,)
    ).fetchall()
    procurement = conn.execute(
        "SELECT * FROM procurement WHERE farmer_id=? ORDER BY id DESC",
        (farmer_id,)
    ).fetchall()
    notifications = conn.execute(
        "SELECT * FROM notifications WHERE farmer_id=? ORDER BY id DESC LIMIT 10",
        (farmer_id,)
    ).fetchall()
    conn.close()

    return render_template(
        "dashboard.html",
        farmer=farmer,
        slots=slots,
        procurement=procurement,
        notifications=notifications
    )


@app.route("/book-slot", methods=["GET", "POST"])
def book_slot():
    if not farmer_required():
        return redirect(url_for("login"))

    if request.method == "POST":
        slot_date = request.form["slot_date"]
        slot_time = request.form["slot_time"]

        if slot_date < str(date.today()):
            flash_key("flash_future_date", "danger")
            return redirect(url_for("book_slot"))

        conn = get_db()

        count = conn.execute(
            "SELECT COUNT(*) FROM slots WHERE slot_date=? AND slot_time=? AND status='Booked'",
            (slot_date, slot_time)
        ).fetchone()[0]

        if count >= 10:
            conn.close()
            flash_key("flash_slot_full", "danger")
            return redirect(url_for("book_slot"))

        existing = conn.execute(
            "SELECT id FROM slots WHERE farmer_id=? AND slot_date=? AND status='Booked'",
            (session["farmer_id"], slot_date)
        ).fetchone()

        if existing:
            conn.close()
            flash_key("flash_duplicate_booking", "warning")
            return redirect(url_for("book_slot"))

        token = f"T{slot_date.replace('-', '')}{count+1:03d}"

        conn.execute(
            """INSERT INTO slots
               (farmer_id, slot_date, slot_time, token, status, created_at)
               VALUES (?, ?, ?, ?, 'Booked', ?)""",
            (session["farmer_id"], slot_date, slot_time, token, datetime.now().isoformat())
        )

        conn.execute(
            "INSERT INTO notifications(farmer_id, message, created_at) VALUES (?, ?, ?)",
            (session["farmer_id"],
             f"Slot confirmed: {slot_date} at {slot_time}. Token: {token}",
             datetime.now().isoformat())
        )
        conn.commit()
        conn.close()

        flash_key("flash_slot_booked", "success", token=token)
        return redirect(url_for("dashboard"))

    return render_template("book_slot.html", today=str(date.today()))


@app.route("/queue")
def queue():
    if not farmer_required():
        return redirect(url_for("login"))

    conn = get_db()
    today = str(date.today())
    rows = conn.execute("""
        SELECT s.*, f.name, f.crop
        FROM slots s
        JOIN farmers f ON f.id=s.farmer_id
        WHERE s.slot_date=? AND s.status='Booked'
        ORDER BY s.slot_time, s.id
    """, (today,)).fetchall()
    conn.close()

    return render_template("queue.html", rows=rows, today=today)


@app.route("/api/queue")
def api_queue():
    conn = get_db()
    rows = conn.execute("""
        SELECT s.token, s.slot_time, s.status, f.name, f.crop
        FROM slots s JOIN farmers f ON f.id=s.farmer_id
        WHERE s.slot_date=? AND s.status='Booked'
        ORDER BY s.slot_time, s.id
    """, (str(date.today()),)).fetchall()
    conn.close()
    payload = []
    for r in rows:
        item = dict(r)
        # Keep database values unchanged, but localize user-visible labels.
        item["crop"] = crop_label(item["crop"])
        item["status"] = status_label(item["status"])
        payload.append(item)
    return jsonify(payload)



# ---------------- VOICE CONNECT: FARMER MESSAGES + ADMIN VOICE REPLIES ----------------
def _voice_extension(upload):
    """Return a supported extension, preferring the browser's uploaded filename."""
    original = secure_filename(upload.filename or "")
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_VOICE_EXTENSIONS:
        mime = (upload.mimetype or "").split(";", 1)[0].lower()
        ext = {
            "audio/webm": ".webm",
            "video/webm": ".webm",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "audio/ogg": ".ogg",
            "application/ogg": ".ogg",
        }.get(mime, "")
    return ext if ext in ALLOWED_VOICE_EXTENSIONS else None


def _save_voice_upload(upload):
    if not upload or not upload.filename:
        return None, "Please record or choose an audio file first."
    upload.stream.seek(0, os.SEEK_END)
    size = upload.stream.tell()
    upload.stream.seek(0)
    if size <= 0:
        return None, "The audio recording is empty. Please record again."
    if size > MAX_VOICE_BYTES:
        return None, "The recording is too large. Maximum size is 10 MB."
    ext = _voice_extension(upload)
    if not ext:
        return None, "Unsupported audio format. Please record again using your browser."
    filename = f"{uuid.uuid4().hex}{ext}"
    destination = VOICE_UPLOAD_DIR / filename
    upload.save(destination)
    return filename, None


@app.route("/voice-messages", methods=["GET", "POST"])
def farmer_voice_messages():
    if not farmer_required():
        return redirect(url_for("login"))
    farmer_id = session["farmer_id"]
    if request.method == "POST":
        filename, error = _save_voice_upload(request.files.get("audio"))
        if error:
            flash(error, "danger")
            return redirect(url_for("farmer_voice_messages"))
        language = request.form.get("language", current_language())
        if language not in SUPPORTED_LANGUAGES:
            language = current_language()
        conn = get_db()
        conn.execute(
            """INSERT INTO voice_messages
               (farmer_id, sender_type, parent_id, audio_filename, language, status, created_at)
               VALUES (?, 'farmer', NULL, ?, ?, 'New', ?)""",
            (farmer_id, filename, language, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        flash("Your voice message was sent to the procurement admin.", "success")
        return redirect(url_for("farmer_voice_messages"))

    conn = get_db()
    messages = conn.execute(
        """SELECT * FROM voice_messages
           WHERE farmer_id=? ORDER BY created_at DESC, id DESC""",
        (farmer_id,)
    ).fetchall()
    farmer = conn.execute("SELECT name FROM farmers WHERE id=?", (farmer_id,)).fetchone()
    conn.close()
    return render_template("voice_messages.html", messages=messages, farmer=farmer)


@app.route("/admin/voice-messages", methods=["GET", "POST"])
@admin_required
def admin_voice_messages():
    if request.method == "POST":
        parent_raw = request.form.get("parent_id", "")
        try:
            parent_id = int(parent_raw)
        except (TypeError, ValueError):
            abort(400)
        conn = get_db()
        parent = conn.execute(
            "SELECT id, farmer_id, sender_type FROM voice_messages WHERE id=?",
            (parent_id,)
        ).fetchone()
        conn.close()
        if not parent or parent["sender_type"] != "farmer":
            abort(404)
        filename, error = _save_voice_upload(request.files.get("audio"))
        if error:
            flash(error, "danger")
            return redirect(url_for("admin_voice_messages"))
        language = request.form.get("language", current_language())
        if language not in SUPPORTED_LANGUAGES:
            language = current_language()
        conn = get_db()
        conn.execute(
            """INSERT INTO voice_messages
               (farmer_id, sender_type, parent_id, audio_filename, language, status, created_at)
               VALUES (?, 'admin', ?, ?, ?, 'Reply', ?)""",
            (parent["farmer_id"], parent_id, filename, language, datetime.now().isoformat())
        )
        conn.execute(
            "UPDATE voice_messages SET status='Replied' WHERE id=?",
            (parent_id,)
        )
        conn.execute(
            "INSERT INTO notifications(farmer_id, message, created_at) VALUES (?, ?, ?)",
            (parent["farmer_id"], "The admin has sent a voice reply to your voice message.", datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        flash("Voice reply sent to the farmer.", "success")
        return redirect(url_for("admin_voice_messages"))

    conn = get_db()
    messages = conn.execute(
        """SELECT vm.*, f.name AS farmer_name, f.phone AS farmer_phone, f.village AS farmer_village
           FROM voice_messages vm
           JOIN farmers f ON f.id=vm.farmer_id
           WHERE vm.sender_type='farmer'
           ORDER BY CASE WHEN vm.status='New' THEN 0 ELSE 1 END, vm.created_at DESC, vm.id DESC"""
    ).fetchall()
    replies = conn.execute(
        """SELECT * FROM voice_messages WHERE sender_type='admin'
           ORDER BY created_at ASC, id ASC"""
    ).fetchall()
    conn.close()
    replies_by_parent = {}
    for reply in replies:
        replies_by_parent.setdefault(reply["parent_id"], []).append(reply)
    return render_template("admin_voice_messages.html", messages=messages, replies_by_parent=replies_by_parent)


@app.route("/voice-audio/<int:message_id>")
def play_voice_audio(message_id):
    # Audio is private: only its farmer or an authenticated admin may listen.
    if not session.get("admin_logged_in") and not session.get("farmer_id"):
        return redirect(url_for("login"))
    conn = get_db()
    message = conn.execute(
        "SELECT farmer_id, audio_filename FROM voice_messages WHERE id=?",
        (message_id,)
    ).fetchone()
    conn.close()
    if not message:
        abort(404)
    if not session.get("admin_logged_in") and session.get("farmer_id") != message["farmer_id"]:
        abort(403)
    path = VOICE_UPLOAD_DIR / message["audio_filename"]
    if not path.is_file():
        abort(404)
    mimetype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    response = make_response(send_file(path, mimetype=mimetype, conditional=True))
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# ---------------- ADMIN AUTHENTICATION ----------------

@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin"))

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            selected_language = current_language()
            session.clear()
            session["language"] = selected_language
            session["admin_logged_in"] = True
            session["admin_username"] = username
            return redirect(url_for("admin"))

        flash_key("flash_invalid_admin_login", "danger")

    return render_template("admin_login.html")


@app.route("/admin-logout")
def admin_logout():
    selected_language = current_language()
    session.clear()
    session["language"] = selected_language
    return redirect(url_for("home"))


@app.route("/admin")
@admin_required
def admin():
    conn = get_db()
    farmers = conn.execute(
        "SELECT id, name, phone, village, crop, created_at FROM farmers ORDER BY id DESC"
    ).fetchall()
    slots = conn.execute("""
        SELECT s.*, f.name, f.phone, f.crop
        FROM slots s JOIN farmers f ON f.id=s.farmer_id
        ORDER BY s.slot_date DESC, s.slot_time DESC
    """).fetchall()
    procurement = conn.execute("""
        SELECT p.*, f.name, f.phone
        FROM procurement p JOIN farmers f ON f.id=p.farmer_id
        ORDER BY p.id DESC
    """).fetchall()
    conn.close()
    return render_template("admin.html", farmers=farmers, slots=slots, procurement=procurement)


@app.route("/admin/procurement", methods=["POST"])
@admin_required
def add_procurement():
    farmer_id = request.form["farmer_id"]
    crop = request.form["crop"]
    quantity = float(request.form["quantity"])
    rate = float(request.form["rate"])
    procurement_status = request.form["procurement_status"]
    payment_status = request.form["payment_status"]
    total = quantity * rate

    conn = get_db()
    conn.execute("""
        INSERT INTO procurement
        (farmer_id, crop, quantity, rate, total, procurement_status, payment_status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (farmer_id, crop, quantity, rate, total,
          procurement_status, payment_status, datetime.now().isoformat()))

    conn.execute("""
        INSERT INTO notifications(farmer_id, message, created_at)
        VALUES (?, ?, ?)
    """, (farmer_id,
          f"Procurement updated: {procurement_status}. Payment: {payment_status}. Amount: ₹{total:.2f}",
          datetime.now().isoformat()))

    conn.commit()
    conn.close()
    flash_key("flash_procurement_added", "success")
    return redirect(url_for("admin"))


@app.route("/admin/slot/<int:slot_id>/<status>")
@admin_required
def update_slot(slot_id, status):
    if status not in ["Booked", "Completed", "Cancelled"]:
        return redirect(url_for("admin"))

    conn = get_db()
    row = conn.execute(
        "SELECT farmer_id, token FROM slots WHERE id=?", (slot_id,)
    ).fetchone()

    if row:
        conn.execute("UPDATE slots SET status=? WHERE id=?", (status, slot_id))
        conn.execute(
            "INSERT INTO notifications(farmer_id, message, created_at) VALUES (?, ?, ?)",
            (row["farmer_id"],
             f"Queue/slot update: Token {row['token']} is now {status}.",
             datetime.now().isoformat())
        )
        conn.commit()
    conn.close()
    flash_key("flash_slot_updated", "success")
    return redirect(url_for("admin"))



# ---------------- PAYMENT TRACKER, CROP MSP, PRICE BARGAINING, RECEIPTS ----------------
@app.route("/payments")
def payment_tracker():
    if not farmer_required():
        return redirect(url_for("login"))
    conn = get_db()
    rows = conn.execute("SELECT * FROM procurement WHERE farmer_id=? ORDER BY id DESC", (session["farmer_id"],)).fetchall()
    summary = conn.execute("""SELECT COALESCE(SUM(total),0) AS total,
        COALESCE(SUM(CASE WHEN payment_status='Paid' THEN total ELSE 0 END),0) AS paid,
        COALESCE(SUM(CASE WHEN payment_status!='Paid' THEN total ELSE 0 END),0) AS pending
        FROM procurement WHERE farmer_id=?""", (session["farmer_id"],)).fetchone()
    conn.close()
    return render_template("payments.html", rows=rows, summary=summary)

@app.route("/crop-prices", methods=["GET", "POST"])
def crop_prices():
    if not farmer_required() and not session.get("admin_logged_in"):
        return redirect(url_for("login"))
    if request.method == "POST":
        if not session.get("admin_logged_in"):
            abort(403)
        crop = request.form.get("crop", "").strip()
        season = request.form.get("season", "").strip()
        try:
            msp = float(request.form.get("msp_per_quintal", "0"))
            market = float(request.form.get("market_price_per_quintal", "0"))
            if not crop or msp < 0 or market < 0:
                raise ValueError
        except ValueError:
            flash("Enter a crop name and valid non-negative prices.", "danger")
            return redirect(url_for("crop_prices"))
        conn = get_db()
        conn.execute("""INSERT INTO crop_prices(crop,msp_per_quintal,market_price_per_quintal,season,updated_at)
            VALUES(?,?,?,?,?) ON CONFLICT(crop) DO UPDATE SET msp_per_quintal=excluded.msp_per_quintal,
            market_price_per_quintal=excluded.market_price_per_quintal,season=excluded.season,updated_at=excluded.updated_at""",
            (crop, msp, market, season, datetime.now().isoformat()))
        conn.commit(); conn.close()
        flash("Crop price information saved. Verify MSP figures against the official government notification before publishing.", "success")
        return redirect(url_for("crop_prices"))
    conn = get_db()
    prices = conn.execute("SELECT * FROM crop_prices ORDER BY crop COLLATE NOCASE").fetchall()
    conn.close()
    return render_template("crop_prices.html", prices=prices)

@app.route("/bargaining", methods=["GET", "POST"])
def price_bargaining():
    is_admin = bool(session.get("admin_logged_in"))
    if not is_admin and not farmer_required():
        return redirect(url_for("login"))
    conn = get_db()
    if request.method == "POST":
        action = request.form.get("action", "submit")
        if is_admin:
            try:
                bargain_id = int(request.form.get("bargain_id", "0"))
                status = request.form.get("status", "Pending")
                admin_offer_raw = request.form.get("admin_offer", "").strip()
                admin_offer = float(admin_offer_raw) if admin_offer_raw else None
                admin_message = request.form.get("admin_message", "").strip()[:1000]
                if status not in {"Accepted", "Rejected", "Counter Offer", "Pending"} or (admin_offer is not None and admin_offer < 0):
                    raise ValueError
            except (ValueError, TypeError):
                conn.close(); flash("Enter a valid admin response.", "danger")
                return redirect(url_for("price_bargaining"))
            conn.execute("UPDATE price_bargains SET status=?, admin_offer=?, admin_message=?, updated_at=? WHERE id=?",
                         (status, admin_offer, admin_message, datetime.now().isoformat(), bargain_id))
            row = conn.execute("SELECT farmer_id, crop FROM price_bargains WHERE id=?", (bargain_id,)).fetchone()
            if row:
                conn.execute("INSERT INTO notifications(farmer_id,message,created_at) VALUES(?,?,?)",
                             (row["farmer_id"], f"Price negotiation update for {row['crop']}: {status}.", datetime.now().isoformat()))
            conn.commit(); conn.close()
            flash("Negotiation response saved.", "success")
            return redirect(url_for("price_bargaining"))
        else:
            try:
                crop = request.form.get("crop", "").strip()
                quantity = float(request.form.get("quantity_quintals", "1"))
                offer = float(request.form.get("farmer_offer", "0"))
                message = request.form.get("farmer_message", "").strip()[:1000]
                if not crop or quantity <= 0 or offer <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                conn.close(); flash("Enter a crop, positive quantity and valid offer price.", "danger")
                return redirect(url_for("price_bargaining"))
            conn.execute("INSERT INTO price_bargains(farmer_id,crop,quantity_quintals,farmer_offer,farmer_message,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                         (session["farmer_id"], crop, quantity, offer, message, datetime.now().isoformat(), datetime.now().isoformat()))
            conn.commit(); conn.close()
            flash("Your offer has been sent to the admin. You can track the response below.", "success")
            return redirect(url_for("price_bargaining"))
    if is_admin:
        bargains = conn.execute("SELECT b.*, f.name AS farmer_name, f.phone, f.village FROM price_bargains b JOIN farmers f ON f.id=b.farmer_id ORDER BY b.id DESC").fetchall()
        prices = conn.execute("SELECT crop FROM crop_prices ORDER BY crop COLLATE NOCASE").fetchall()
    else:
        bargains = conn.execute("SELECT * FROM price_bargains WHERE farmer_id=? ORDER BY id DESC", (session["farmer_id"],)).fetchall()
        prices = conn.execute("SELECT crop FROM crop_prices ORDER BY crop COLLATE NOCASE").fetchall()
    conn.close()
    return render_template("bargaining.html", bargains=bargains, prices=prices, is_admin=is_admin)

@app.route("/farmer-id")
def farmer_id_card():
    if not farmer_required():
        return redirect(url_for("login"))
    conn = get_db()
    farmer = conn.execute("SELECT id,name,phone,village,crop,created_at FROM farmers WHERE id=?", (session["farmer_id"],)).fetchone()
    conn.close()
    return render_template("farmer_id.html", farmer=farmer)

@app.route("/receipt/<int:procurement_id>")
def procurement_receipt(procurement_id):
    if not farmer_required(): return redirect(url_for("login"))
    conn = get_db()
    record = conn.execute("""SELECT p.*, f.name, f.phone, f.village, f.id AS farmer_number
        FROM procurement p JOIN farmers f ON f.id=p.farmer_id
        WHERE p.id=? AND p.farmer_id=?""", (procurement_id, session["farmer_id"])).fetchone()
    conn.close()
    if not record: abort(404)
    return render_template("receipt.html", record=record, receipt_number=f"KS-{record['id']:06d}")


@app.route("/admin/seed-demo")
@admin_required
def seed_demo():
    conn = get_db()
    existing = conn.execute("SELECT COUNT(*) FROM farmers").fetchone()[0]
    if existing == 0:
        conn.execute(
            "INSERT INTO farmers(name, phone, village, crop, password, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("Demo Farmer", "9999999999", "Green Village", "Wheat",
             generate_password_hash("123456"), datetime.now().isoformat())
        )
        conn.commit()
    conn.close()
    flash_key("flash_demo_created", "success")
    return redirect(url_for("admin"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
