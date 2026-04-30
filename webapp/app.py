"""
GradeSentinel WebApp — Grade Dashboard
Lightweight Flask server for Telegram Mini App integration.
"""

import os
import sys
import hmac
import hashlib
import json
import logging
from urllib.parse import parse_qs
from flask import Flask, render_template, jsonify, request, abort

# Add parent directory to path for src imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database_manager import (
    init_db,
    get_students_for_parent,
    get_grade_history_for_student_all,
    get_parent_role,
    get_quarter_grades,
)
from src.db.auth import is_student_under_active_subscription

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")


def validate_init_data(init_data: str) -> dict:
    """
    Validates Telegram WebApp initData according to official docs.
    Returns parsed user data if valid, raises ValueError otherwise.
    """
    if not init_data or not BOT_TOKEN:
        raise ValueError("Missing initData or BOT_TOKEN")

    parsed = parse_qs(init_data)
    check_hash = parsed.get("hash", [None])[0]
    if not check_hash:
        raise ValueError("No hash in initData")

    # Build data_check_string (sorted key=value pairs, excluding hash)
    data_pairs = []
    for pair in init_data.split("&"):
        key = pair.split("=")[0]
        if key != "hash":
            data_pairs.append(pair)
    data_pairs.sort()
    data_check_string = "\n".join(data_pairs)

    # HMAC-SHA256 with secret_key = HMAC-SHA256("WebAppData", bot_token)
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if computed_hash != check_hash:
        raise ValueError("Invalid hash")

    # Parse user info
    user_json = parsed.get("user", [None])[0]
    if user_json:
        return json.loads(user_json)
    raise ValueError("No user data")


@app.route("/webapp")
def dashboard():
    """Serves the main dashboard HTML page."""
    return render_template("dashboard.html")


@app.route("/api/students")
def api_students():
    """Returns list of students for the authenticated user."""
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    try:
        user = validate_init_data(init_data)
        telegram_id = user["id"]
    except (ValueError, KeyError) as e:
        logger.warning(f"WebApp auth failed: {e}")
        abort(401)

    students = get_students_for_parent(telegram_id)
    return jsonify([
        {"id": s["id"], "fio": s["fio"], "display_name": s.get("display_name") or s["fio"]}
        for s in students
    ])


def _authorize_student_access(student_id: int):
    """Возвращает telegram_id если у пользователя есть доступ к ученику
    И у его семьи активная подписка. Иначе вызывает abort()."""
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    try:
        user = validate_init_data(init_data)
        telegram_id = user["id"]
    except (ValueError, KeyError) as e:
        logger.warning(f"WebApp auth failed: {e}")
        abort(401)

    students = get_students_for_parent(telegram_id)
    student_ids = [s["id"] for s in students]
    if student_id not in student_ids:
        abort(403)

    # Админ обходит проверку подписки
    if get_parent_role(telegram_id) != 'admin':
        if not is_student_under_active_subscription(student_id):
            logger.info(f"WebApp denied: tg={telegram_id} student={student_id} (no active subscription)")
            abort(402)  # Payment Required

    return telegram_id


@app.route("/api/grades/<int:student_id>")
def api_grades(student_id):
    """Returns grade history for a specific student."""
    _authorize_student_access(student_id)

    days = request.args.get("days", 30, type=int)
    days = min(days, 365)  # Increased cap: full year with historical import

    subject = request.args.get("subject", "").strip()

    grades = get_grade_history_for_student_all(student_id, days=days)

    # Фильтр по предмету (P3)
    if subject:
        grades = [g for g in grades if g['subject'] == subject]

    return jsonify(grades)


@app.route("/api/quarters/<int:student_id>")
def api_quarters(student_id):
    """Returns quarter grades for a specific student."""
    _authorize_student_access(student_id)

    quarters = get_quarter_grades(student_id)
    return jsonify(quarters)


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("WEBAPP_PORT", 8443))
    app.run(host="0.0.0.0", port=port, debug=False)
