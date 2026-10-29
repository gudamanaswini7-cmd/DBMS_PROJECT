"""Flask application for the EcoCycle e-waste collection project."""

import datetime
import math
import os
import sqlite3
import uuid
from functools import wraps

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "ewaste.db")
SQL_FILE = os.path.join(BASE_DIR, "database.sql")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
REWARD_PER_KG = 2.5

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get(
        "SECRET_KEY", "ecocycle-development-key-change-before-deployment"
    ),
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


def get_db_connection():
    """Open a SQLite connection with named columns and foreign keys enabled."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_exists(conn, table_name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _table_columns(conn, table_name):
    if not _table_exists(conn, table_name):
        return set()
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table_name}")')}


def _table_rows(conn, table_name):
    if not _table_exists(conn, table_name):
        return []
    return [dict(row) for row in conn.execute(f'SELECT * FROM "{table_name}"')]


def _pick(row, *column_names, default=None):
    for column_name in column_names:
        if column_name in row and row[column_name] is not None:
            return row[column_name]
    return default


def _legacy_snapshot(conn):
    """Read the previous project schema before replacing it with the faculty schema."""
    return {
        "users": _table_rows(conn, "users"),
        "items": _table_rows(conn, "ewaste_items"),
        "pickup_requests": _table_rows(conn, "pickup_requests"),
        "collections": _table_rows(conn, "collections"),
        "collection_details": _table_rows(conn, "collection_details"),
        "centers": _table_rows(conn, "centers"),
        "payments": _table_rows(conn, "payments"),
    }


def _seed_demo_accounts(conn):
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return
    conn.executemany(
        "INSERT INTO users (name, email, password, category) VALUES (?, ?, ?, ?)",
        [
            (
                "EcoCycle Staff",
                "staff@test.com",
                generate_password_hash("123"),
                "staff",
            ),
            (
                "Priya Sharma",
                "priya@test.com",
                generate_password_hash("123"),
                "citizen",
            ),
        ],
    )


def _run_schema(conn):
    with open(SQL_FILE, "r", encoding="utf-8") as schema_file:
        schema = schema_file.read()

    statement = ""
    for line in schema.splitlines():
        statement += line + "\n"
        if sqlite3.complete_statement(statement):
            sql = statement.strip()
            statement = ""
            if sql:
                conn.execute(sql)
    if statement.strip():
        raise sqlite3.OperationalError("database.sql contains an incomplete SQL statement")


def _is_current_schema(conn):
    expected = {
        "users": {"id", "name", "email", "password", "category"},
        "ewaste_items": {
            "id",
            "user_id",
            "item_type",
            "weight",
            "description",
            "image_path",
            "status",
            "created_at",
        },
        "collections": {
            "id",
            "user_id",
            "item_id",
            "pickup_date",
            "pickup_address",
            "status",
        },
        "transactions": {
            "id",
            "user_id",
            "item_id",
            "weight",
            "reward_amount",
            "date",
        },
    }
    return all(_table_columns(conn, table) == columns for table, columns in expected.items())


def _migrate_legacy_data(conn, snapshot):
    user_ids = {}
    for user in snapshot["users"]:
        old_id = _pick(user, "id", "user_id")
        if old_id is not None:
            user_ids[old_id] = old_id

    for user in snapshot["users"]:
        user_id = _pick(user, "id", "user_id")
        if user_id is None:
            continue
        role = str(_pick(user, "category", "role", default="citizen")).lower()
        category = "staff" if role in ("staff", "admin") else "citizen"
        email = str(_pick(user, "email", default=f"user-{user_id}@invalid.local"))
        raw_password = str(_pick(user, "password", default=""))
        try:
            check_password_hash(raw_password, "")
            password = raw_password
        except (ValueError, TypeError):
            password = generate_password_hash(raw_password)
        conn.execute(
            """
            INSERT OR IGNORE INTO users (id, name, email, password, category)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                str(_pick(user, "name", default="User")),
                email,
                password,
                category,
            ),
        )

    item_ids = {}
    old_to_new_item = {}
    for item in snapshot["items"]:
        item_id = _pick(item, "id", "item_id")
        owner_id = _pick(item, "user_id")
        if item_id is None or owner_id not in user_ids:
            continue
        old_to_new_item[item_id] = item_id
        status = str(_pick(item, "status", default="pending")).lower()
        mapped_status = "completed" if status in ("processed", "completed") else "pending"
        created_at = _pick(item, "created_at", "date", default=datetime.date.today().isoformat())
        conn.execute(
            """
            INSERT OR IGNORE INTO ewaste_items
                (id, user_id, item_type, weight, description, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                owner_id,
                str(_pick(item, "item_type", "type", default="E-waste item")),
                float(_pick(item, "weight", default=0.01)),
                str(_pick(item, "description", default="")),
                mapped_status,
                str(created_at),
            ),
        )
        item_ids[item_id] = owner_id

    for pickup in snapshot["pickup_requests"]:
        user_id = _pick(pickup, "user_id")
        item_id = _pick(pickup, "item_id")
        if user_id not in user_ids or item_ids.get(item_id) != user_id:
            continue
        status = str(_pick(pickup, "status", default="pending")).lower()
        if status == "rejected":
            continue
        mapped_status = "completed" if status in ("completed", "processed") else "pending"
        conn.execute(
            """
            INSERT INTO collections
                (id, user_id, item_id, pickup_date, pickup_address, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _pick(pickup, "id", "request_id"),
                user_id,
                item_id,
                str(
                    _pick(
                        pickup,
                        "pickup_date",
                        "preferred_date",
                        default=datetime.date.today().isoformat(),
                    )
                ),
                str(_pick(pickup, "pickup_address", "address", default="Address not provided")),
                mapped_status,
            ),
        )

    details_by_collection = {}
    for detail in snapshot["collection_details"]:
        details_by_collection.setdefault(
            _pick(detail, "collection_id"), []
        ).append(_pick(detail, "item_id"))
    center_locations = {
        _pick(center, "center_id", "id"): _pick(center, "location", default="")
        for center in snapshot["centers"]
    }
    for old_collection in snapshot["collections"]:
        old_collection_id = _pick(old_collection, "id", "collection_id")
        user_id = _pick(old_collection, "user_id")
        if user_id not in user_ids:
            continue
        for item_id in details_by_collection.get(old_collection_id, []):
            if item_ids.get(item_id) != user_id:
                continue
            conn.execute(
                """
                INSERT INTO collections
                    (user_id, item_id, pickup_date, pickup_address, status)
                VALUES (?, ?, ?, ?, 'completed')
                """,
                (
                    user_id,
                    item_id,
                    str(_pick(old_collection, "date", "pickup_date", default="")),
                    str(
                        center_locations.get(
                            _pick(old_collection, "center_id"), "Previous collection"
                        )
                    ),
                ),
            )

    completed_item_ids = set()
    for collection in conn.execute(
        "SELECT user_id, item_id FROM collections WHERE status = 'completed'"
    ):
        completed_item_ids.add((collection["user_id"], collection["item_id"]))
    for user_id, item_id in completed_item_ids:
        item = conn.execute(
            "SELECT weight FROM ewaste_items WHERE id = ? AND user_id = ?",
            (item_id, user_id),
        ).fetchone()
        if item is None:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO transactions
                (user_id, item_id, weight, reward_amount, date)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (user_id, item_id, item["weight"], round(item["weight"] * REWARD_PER_KG, 2)),
        )


def init_db():
    """Create the requested schema and migrate useful records from earlier versions."""
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        if _is_current_schema(conn):
            _run_schema(conn)
            conn.commit()
            return

        snapshot = _legacy_snapshot(conn)
        conn.execute("BEGIN IMMEDIATE")
        existing_tables = [
            row[0]
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            ).fetchall()
        ]
        for table_name in existing_tables:
            conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        _run_schema(conn)
        _migrate_legacy_data(conn, snapshot)
        _seed_demo_accounts(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def role_required(category):
    def decorate(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if "user_id" not in session:
                flash("Please sign in to continue.", "warning")
                return redirect(url_for("login"))
            if session.get("category") != category:
                flash("You do not have permission to access that page.", "danger")
                return redirect(url_for("index"))
            conn = get_db_connection()
            try:
                account = conn.execute(
                    "SELECT category FROM users WHERE id = ?",
                    (session["user_id"],),
                ).fetchone()
            finally:
                conn.close()
            if account is None or account["category"] != category:
                session.clear()
                flash("Your account is no longer available. Please sign in again.", "danger")
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return wrapped_view

    return decorate


citizen_required = role_required("citizen")
staff_required = role_required("staff")


def _login_destination(category):
    return url_for("staff_dashboard") if category == "staff" else url_for("citizen_dashboard")


@app.route("/")
def index():
    category = session.get("category")
    if category in ("citizen", "staff"):
        return redirect(_login_destination(category))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        category = request.form.get("category", "").strip().lower()
        if category not in ("staff", "citizen"):
            flash("Select Staff or Citizen as your account category.", "danger")
            return render_template("login.html")

        conn = get_db_connection()
        try:
            user = conn.execute(
                "SELECT id, name, email, password, category FROM users WHERE email = ?",
                (email,),
            ).fetchone()
            valid_password = False
            if user:
                try:
                    valid_password = check_password_hash(user["password"], password)
                except (ValueError, TypeError):
                    valid_password = False
                if not valid_password:
                    valid_password = user["password"] == password

            if user and valid_password and user["category"] == category:
                if not user["password"].startswith(("scrypt:", "pbkdf2:")):
                    conn.execute(
                        "UPDATE users SET password = ? WHERE id = ?",
                        (generate_password_hash(password), user["id"]),
                    )
                    conn.commit()
                session.clear()
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                session["user_email"] = user["email"]
                session["category"] = user["category"]
                flash(f"Welcome back, {user['name']}!", "success")
                return redirect(_login_destination(user["category"]))
            flash("Email, password, or category is incorrect.", "danger")
        finally:
            conn.close()

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        category = request.form.get("category", "").strip().lower()

        if not name or not email or not password:
            flash("Please complete all required fields.", "danger")
            return render_template("registration.html")
        if password != confirm_password:
            flash("Password and confirmation do not match.", "danger")
            return render_template("registration.html")
        if category not in ("staff", "citizen"):
            flash("Select Staff or Citizen as your category.", "danger")
            return render_template("registration.html")

        conn = get_db_connection()
        try:
            conn.execute(
                """
                INSERT INTO users (name, email, password, category)
                VALUES (?, ?, ?, ?)
                """,
                (name, email, generate_password_hash(password), category),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            flash("An account with this email already exists.", "danger")
            return render_template("registration.html")
        finally:
            conn.close()

        flash("Your account has been created. Please sign in.", "success")
        return redirect(url_for("login"))

    return render_template("registration.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("login"))


@app.route("/citizen/dashboard")
@citizen_required
def citizen_dashboard():
    user_id = session["user_id"]
    conn = get_db_connection()
    try:
        stats = conn.execute(
            """
            SELECT COUNT(*) AS item_count,
                   COALESCE(SUM(weight), 0) AS total_weight
            FROM ewaste_items WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        rewards = conn.execute(
            "SELECT COALESCE(SUM(reward_amount), 0) FROM transactions WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        items = conn.execute(
            """
            SELECT id, item_type, weight, description, status, created_at, image_path
            FROM ewaste_items WHERE user_id = ?
            ORDER BY id DESC LIMIT 10
            """,
            (user_id,),
        ).fetchall()
    finally:
        conn.close()

    return render_template(
        "citizen_dashboard.html",
        citizen_name=session.get("user_name", "Citizen"),
        stats=stats,
        rewards=round(rewards, 2),
        items=items,
        active_module="dashboard",
    )


@app.route("/citizen/new-waste", methods=["GET", "POST"])
@citizen_required
def citizen_new_waste():
    if request.method == "POST":
        item_type = request.form.get("item_type", "").strip()
        description = request.form.get("description", "").strip()
        pickup_address = request.form.get("pickup_address", "").strip()
        pickup_date = request.form.get("pickup_date", "").strip()
        image = request.files.get("image")

        try:
            weight = float(request.form.get("weight", ""))
        except (ValueError, TypeError):
            weight = 0.0
        try:
            pickup_day = datetime.date.fromisoformat(pickup_date)
        except ValueError:
            pickup_day = None

        if (
            not item_type
            or not math.isfinite(weight)
            or weight <= 0
            or weight > 10000
            or not pickup_address
            or pickup_day is None
            or pickup_day < datetime.date.today()
        ):
            flash(
                "Enter a valid item, positive weight, pickup address, and a date today or later.",
                "danger",
            )
            return render_template(
                "citizen_dashboard.html",
                citizen_name=session.get("user_name", "Citizen"),
                active_module="new-waste",
                today=datetime.date.today().isoformat(),
            )

        image_path = None
        if image and image.filename:
            original_name = secure_filename(image.filename)
            extension = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
            if extension not in ALLOWED_IMAGE_EXTENSIONS:
                flash("Upload a JPG, PNG, or WEBP image.", "danger")
                return render_template(
                    "citizen_dashboard.html",
                    citizen_name=session.get("user_name", "Citizen"),
                    active_module="new-waste",
                    today=datetime.date.today().isoformat(),
                )
            filename = f"{uuid.uuid4().hex}.{extension}"
            image.save(os.path.join(UPLOAD_FOLDER, filename))
            image_path = f"uploads/{filename}"

        conn = get_db_connection()
        try:
            cursor = conn.execute(
                """
                INSERT INTO ewaste_items
                    (user_id, item_type, weight, description, image_path, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (session["user_id"], item_type, weight, description, image_path),
            )
            item_id = cursor.lastrowid
            conn.execute(
                """
                INSERT INTO collections
                    (user_id, item_id, pickup_date, pickup_address, status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (session["user_id"], item_id, pickup_date, pickup_address),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            if image_path:
                try:
                    os.remove(os.path.join(UPLOAD_FOLDER, os.path.basename(image_path)))
                except FileNotFoundError:
                    pass
            raise
        finally:
            conn.close()

        flash("Your e-waste item and pickup request have been submitted.", "success")
        return redirect(url_for("citizen_dashboard"))

    return render_template(
        "citizen_dashboard.html",
        citizen_name=session.get("user_name", "Citizen"),
        active_module="new-waste",
        today=datetime.date.today().isoformat(),
    )


@app.route("/staff/dashboard")
@staff_required
def staff_dashboard():
    conn = get_db_connection()
    try:
        stats = {
            "citizens": conn.execute(
                "SELECT COUNT(*) FROM users WHERE category = 'citizen'"
            ).fetchone()[0],
            "waste_collected": round(
                conn.execute(
                    """
                    SELECT COALESCE(SUM(t.weight), 0)
                    FROM transactions AS t
                    JOIN users AS u ON u.id = t.user_id
                    WHERE u.category = 'citizen'
                    """
                ).fetchone()[0],
                2,
            ),
            "pending_collections": conn.execute(
                "SELECT COUNT(*) FROM collections WHERE status = 'pending'"
            ).fetchone()[0],
            "transactions": conn.execute(
                "SELECT COUNT(*) FROM transactions"
            ).fetchone()[0],
        }
        category_totals = conn.execute(
            """
            SELECT e.item_type, COALESCE(SUM(t.weight), 0) AS weight
            FROM ewaste_items AS e
            LEFT JOIN transactions AS t ON t.item_id = e.id
            GROUP BY e.item_type ORDER BY weight DESC, e.item_type LIMIT 6
            """
        ).fetchall()
        recent_collections = conn.execute(
            """
            SELECT c.id AS collection_id, c.pickup_date, c.pickup_address,
                   u.name AS citizen_name, e.item_type, e.weight
            FROM collections AS c
            JOIN users AS u ON u.id = c.user_id
            JOIN ewaste_items AS e ON e.id = c.item_id
            WHERE c.status = 'pending'
            ORDER BY c.pickup_date, c.id LIMIT 6
            """
        ).fetchall()
    finally:
        conn.close()
    chart_max_weight = max(
        (float(row["weight"]) for row in category_totals),
        default=1.0,
    )
    return render_template(
        "staff_dashboard.html",
        staff_name=session.get("user_name", "Staff"),
        active_module="dashboard",
        stats=stats,
        category_totals=category_totals,
        chart_max_weight=chart_max_weight or 1.0,
        recent_collections=recent_collections,
    )


@app.route("/staff/collections", methods=["GET", "POST"])
@staff_required
def staff_collections():
    if request.method == "POST":
        try:
            collection_id = int(request.form.get("collection_id", ""))
        except (ValueError, TypeError):
            flash("Choose a valid collection.", "danger")
            return redirect(url_for("staff_collections"))

        conn = get_db_connection()
        try:
            collection = conn.execute(
                """
                SELECT c.id, c.user_id, c.item_id, e.weight
                FROM collections AS c
                JOIN ewaste_items AS e ON e.id = c.item_id
                WHERE c.id = ? AND c.status = 'pending'
                """,
                (collection_id,),
            ).fetchone()
            if collection is None:
                flash("This collection was already completed or was not found.", "warning")
                return redirect(url_for("staff_collections"))

            conn.execute(
                "UPDATE collections SET status = 'completed' WHERE id = ?",
                (collection_id,),
            )
            conn.execute(
                "UPDATE ewaste_items SET status = 'completed' WHERE id = ?",
                (collection["item_id"],),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO transactions
                    (user_id, item_id, weight, reward_amount, date)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    collection["user_id"],
                    collection["item_id"],
                    collection["weight"],
                    round(collection["weight"] * REWARD_PER_KG, 2),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        flash("Collection completed and eco reward recorded.", "success")
        return redirect(url_for("staff_collections"))

    conn = get_db_connection()
    try:
        collections = conn.execute(
            """
            SELECT c.id AS collection_id, c.user_id, c.item_id,
                   c.pickup_date, c.pickup_address, c.status,
                   u.name AS citizen_name, u.email AS citizen_email,
                   e.item_type, e.weight, e.description
            FROM collections AS c
            JOIN users AS u ON u.id = c.user_id
            JOIN ewaste_items AS e ON e.id = c.item_id
            WHERE c.status = 'pending'
            ORDER BY c.pickup_date, c.id
            """
        ).fetchall()
    finally:
        conn.close()
    return render_template(
        "staff_dashboard.html",
        staff_name=session.get("user_name", "Staff"),
        active_module="collections",
        collections=collections,
    )


@app.route("/staff/transactions")
@staff_required
def staff_transactions():
    conn = get_db_connection()
    try:
        transactions = conn.execute(
            """
            SELECT t.id, t.weight, t.reward_amount, t.date,
                   u.name AS citizen_name, u.email AS citizen_email,
                   e.item_type
            FROM transactions AS t
            JOIN users AS u ON u.id = t.user_id
            JOIN ewaste_items AS e ON e.id = t.item_id
            ORDER BY t.id DESC
            """
        ).fetchall()
    finally:
        conn.close()
    return render_template(
        "staff_dashboard.html",
        staff_name=session.get("user_name", "Staff"),
        active_module="transactions",
        transactions=transactions,
    )


@app.errorhandler(413)
def upload_too_large(error):
    if error.code == 413:
        flash("Image files must be smaller than 8 MB.", "danger")
        return (
            render_template(
                "citizen_dashboard.html",
                citizen_name=session.get("user_name", "Citizen"),
                active_module="new-waste",
                today=datetime.date.today().isoformat(),
            ),
            413,
        )
    return error


if __name__ == "__main__":
    init_db()
    print("EcoCycle E-Waste Management System: http://127.0.0.1:5000")
    print("Demo staff: staff@test.com / 123 (select Staff)")
    print("Demo citizen: priya@test.com / 123 (select Citizen)")
    app.run(host="127.0.0.1", port=5000, debug=True)
