"""
GradeSentinel WebApp — Telegram Mini App для родителей.

Один основной endpoint `/api/dashboard/<student_id>?days=N` отдаёт всё что
нужно дашборду за один roundtrip: сводные метрики, тренд по дням, разбивка
по предметам, последние оценки, информацию о юзере (язык, роль, имя).

Старые endpoints (`/api/students`, `/api/grades`, `/api/quarters`) сохранены
для обратной совместимости и для четвертных оценок (lazy-load).
"""

import os
import sys
import hmac
import hashlib
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from urllib.parse import parse_qs
from flask import Flask, render_template, jsonify, request, abort

# Add parent directory to path for src imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database_manager import (
    init_db,
    get_students_for_parent,
    get_grade_history_for_student_all,
    get_parent_role,
    get_user_lang,
    get_quarter_grades,
    get_user_info_by_tg_id,
)
from src.db.auth import is_student_under_active_subscription

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ── Sentry (опционально) ─────────────────────────────────────
_SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration

        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            environment=os.environ.get("ENVIRONMENT", "production"),
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.0,
            send_default_pii=False,
        )
        logger.info("WebApp Sentry initialized")
    except ImportError:
        logger.warning("SENTRY_DSN задан, но sentry_sdk не установлен")
    except Exception as e:
        logger.error(f"Sentry init failed: {e}")

# ── Init на module-level (для gunicorn) ──────────────────────
# При запуске под gunicorn `app.run()` не вызывается, поэтому init_db
# нужно дёрнуть здесь. Идемпотентно — безопасно для повторного импорта.
init_db()

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")


# ════════════════════════════════════════════════════════════
#  AUTH
# ════════════════════════════════════════════════════════════

def validate_init_data(init_data: str) -> dict:
    """
    Validates Telegram WebApp initData per official spec.
    Returns parsed user dict if valid, raises ValueError otherwise.
    """
    if not init_data or not BOT_TOKEN:
        raise ValueError("Missing initData or BOT_TOKEN")

    parsed = parse_qs(init_data)
    check_hash = parsed.get("hash", [None])[0]
    if not check_hash:
        raise ValueError("No hash in initData")

    # data_check_string: URL-decoded values, sorted, joined by \n.
    # Исключаем ТОЛЬКО hash; signature остаётся (Ed25519 для third-party,
    # Telegram включает его в HMAC compute).
    data_pairs = [
        f"{k}={v[0]}" for k, v in parsed.items() if k != "hash"
    ]
    data_pairs.sort()
    data_check_string = "\n".join(data_pairs)

    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if computed_hash != check_hash:
        raise ValueError("Invalid hash")

    user_json = parsed.get("user", [None])[0]
    if user_json:
        return json.loads(user_json)
    raise ValueError("No user data")


def _get_authenticated_user():
    """Извлекает и валидирует юзера из X-Telegram-Init-Data header.
    Возвращает dict с {telegram_id, language_code (TG client lang)} или abort(401)."""
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    try:
        user = validate_init_data(init_data)
        return {
            "telegram_id": user["id"],
            "tg_language_code": user.get("language_code", ""),
        }
    except (ValueError, KeyError) as e:
        logger.warning(f"WebApp auth failed: {e}")
        abort(401)


def _authorize_student_access(student_id: int) -> int:
    """Возвращает telegram_id если у юзера есть доступ к ученику И семья
    с активной подпиской. Иначе abort(401/403/402).
    Админ обходит проверку подписки."""
    auth = _get_authenticated_user()
    telegram_id = auth["telegram_id"]

    students = get_students_for_parent(telegram_id)
    student_ids = [s["id"] for s in students]
    if student_id not in student_ids:
        abort(403)

    if get_parent_role(telegram_id) != 'admin':
        if not is_student_under_active_subscription(student_id):
            logger.info(f"WebApp denied: tg={telegram_id} student={student_id} (no active subscription)")
            abort(402)

    return telegram_id


# ════════════════════════════════════════════════════════════
#  АГРЕГАЦИЯ МЕТРИК (pure functions — легко тестируется)
# ════════════════════════════════════════════════════════════

# Пороговые значения для статуса дашборда
GRADE_PROBLEM_THRESHOLD = 3.5   # avg <= → проблемная тема
GRADE_GOOD_THRESHOLD = 4.5      # avg >= → топ
DELTA_SIGNIFICANT = 0.2         # |delta| >= → заметное изменение


def _avg(values):
    """Среднее арифметическое или None для пустого списка."""
    if not values:
        return None
    return sum(values) / len(values)


def compute_summary(grades_current, grades_previous, period_days):
    """
    Вычисляет hero-метрики дашборда: средний балл, дельта, тренд, статус,
    проблемные/топовые предметы.

    grades_current: оценки за текущий период (list[dict])
    grades_previous: оценки за предыдущий период такой же длины (для дельты)
    period_days: длина периода в днях (для метаданных)
    """
    numeric_current = [g["grade_value"] for g in grades_current if g.get("grade_value") is not None]
    numeric_previous = [g["grade_value"] for g in grades_previous if g.get("grade_value") is not None]

    avg_current = _avg(numeric_current)
    avg_previous = _avg(numeric_previous)

    delta = None
    trend = "stable"
    if avg_current is not None and avg_previous is not None:
        delta = round(avg_current - avg_previous, 2)
        if delta >= DELTA_SIGNIFICANT:
            trend = "up"
        elif delta <= -DELTA_SIGNIFICANT:
            trend = "down"

    # Группировка по предметам
    by_subject_vals = defaultdict(list)
    for g in grades_current:
        if g.get("grade_value") is not None:
            by_subject_vals[g["subject"]].append(g["grade_value"])

    subject_stats = []
    for subj, vals in by_subject_vals.items():
        subject_stats.append({
            "name": subj,
            "avg": round(sum(vals) / len(vals), 2),
            "count": len(vals),
        })

    # Сравнение с предыдущим периодом — для delta по каждому предмету
    by_subject_prev = defaultdict(list)
    for g in grades_previous:
        if g.get("grade_value") is not None:
            by_subject_prev[g["subject"]].append(g["grade_value"])

    for s in subject_stats:
        prev_vals = by_subject_prev.get(s["name"])
        if prev_vals:
            prev_avg = sum(prev_vals) / len(prev_vals)
            s["delta"] = round(s["avg"] - prev_avg, 2)
        else:
            s["delta"] = None

    # Проблемные = avg <= 3.5, sorted ascending (худшие первые)
    problem_subjects = sorted(
        [s for s in subject_stats if s["avg"] <= GRADE_PROBLEM_THRESHOLD],
        key=lambda s: s["avg"],
    )[:5]

    # Топовые = avg >= 4.5, sorted descending
    top_subjects = sorted(
        [s for s in subject_stats if s["avg"] >= GRADE_GOOD_THRESHOLD],
        key=lambda s: -s["avg"],
    )[:5]

    # Общий статус: priority concern > improving > stable
    if problem_subjects:
        status = "concern"
    elif trend == "up":
        status = "improving"
    elif trend == "down":
        status = "declining"
    else:
        status = "stable"

    today = datetime.now().date()
    period_start = (today - timedelta(days=period_days)).isoformat()
    period_end = today.isoformat()

    return {
        "current_avg": round(avg_current, 2) if avg_current is not None else None,
        "previous_avg": round(avg_previous, 2) if avg_previous is not None else None,
        "delta": delta,
        "trend": trend,
        "status": status,
        "period_start": period_start,
        "period_end": period_end,
        "period_days": period_days,
        "new_count": len(grades_current),
        "problem_subjects": problem_subjects,
        "top_subjects": top_subjects,
    }


def compute_trend_by_day(grades, period_days):
    """
    Группирует оценки по дням, возвращает [{date, avg, count}] за весь период.
    Дни без оценок пропускаются (line chart рисует только реальные точки).
    """
    by_date = defaultdict(list)
    for g in grades:
        if g.get("grade_value") is None:
            continue
        # date_added в формате "YYYY-MM-DD HH:MM:SS"
        date_str = g["date_added"][:10] if g.get("date_added") else None
        if date_str:
            by_date[date_str].append(g["grade_value"])

    return [
        {
            "date": date,
            "avg": round(sum(vals) / len(vals), 2),
            "count": len(vals),
        }
        for date, vals in sorted(by_date.items())
    ]


def compute_by_subject(grades):
    """Разбивка по предметам, отсортированная по среднему DESC."""
    by_subj = defaultdict(list)
    for g in grades:
        if g.get("grade_value") is None:
            continue
        by_subj[g["subject"]].append(g["grade_value"])

    return sorted(
        [
            {
                "name": subj,
                "avg": round(sum(vals) / len(vals), 2),
                "count": len(vals),
            }
            for subj, vals in by_subj.items()
        ],
        key=lambda s: -s["avg"],
    )


# ════════════════════════════════════════════════════════════
#  ROUTES — основные
# ════════════════════════════════════════════════════════════

@app.route("/webapp")
def dashboard():
    """Serves the main dashboard HTML page."""
    return render_template("dashboard.html")


@app.route("/api/dashboard/<int:student_id>")
def api_dashboard(student_id):
    """
    Главный endpoint дашборда. Один запрос — все данные:
      - summary (hero метрики)
      - trend_by_day (для line chart)
      - by_subject (для таблицы)
      - recent_grades (последние 50)
      - user (lang, first_name, is_admin)

    Query params:
      days — длина периода (по умолчанию 7, max 365)
    """
    telegram_id = _authorize_student_access(student_id)

    days = request.args.get("days", 7, type=int)
    days = max(1, min(days, 365))

    # Тащим за days*2 чтобы посчитать delta vs предыдущий период
    all_grades = get_grade_history_for_student_all(student_id, days=days * 2)

    # Разделение на current и previous по date cutoff
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_iso = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    grades_current = [g for g in all_grades if g.get("date_added", "") >= cutoff_iso]
    grades_previous = [g for g in all_grades if g.get("date_added", "") < cutoff_iso]

    summary = compute_summary(grades_current, grades_previous, days)
    trend_by_day = compute_trend_by_day(grades_current, days)
    by_subject = compute_by_subject(grades_current)

    # User info — для приветствия и определения языка
    user_info = get_user_info_by_tg_id(telegram_id) or {}
    lang = get_user_lang(telegram_id)
    first_name = ""
    if user_info.get("fio"):
        first_name = user_info["fio"].split()[0]

    # AI-инсайт (опционально, кэш 6h, безопасно деградирует если Claude недоступен)
    try:
        from src.analytics_engine import compute_dashboard_insight
        summary["ai_insight"] = compute_dashboard_insight(student_id, summary, lang=lang, days=days)
    except Exception as e:
        # Никогда не блокируем dashboard из-за AI
        logger.warning(f"AI insight failed for student {student_id}: {e}")
        summary["ai_insight"] = None

    response_data = {
        "summary": summary,
        "trend_by_day": trend_by_day,
        "by_subject": by_subject,
        "recent_grades": grades_current[:50],
        "user": {
            "lang": lang,
            "first_name": first_name,
            "is_admin": user_info.get("role") == "admin",
        },
    }

    return jsonify(response_data)


@app.route("/api/dashboard/init")
def api_dashboard_init():
    """
    Bootstrap endpoint: список студентов + язык юзера + имя.
    Дашборд вызывает его первым, чтобы знать какого студента запрашивать.
    """
    auth = _get_authenticated_user()
    telegram_id = auth["telegram_id"]

    students = get_students_for_parent(telegram_id)
    if not students:
        # Юзер залогинен но без детей — это нормальный кейс
        # (например, глава семьи без добавленных учеников).
        return jsonify({
            "students": [],
            "user": {
                "lang": get_user_lang(telegram_id),
                "first_name": "",
                "is_admin": get_parent_role(telegram_id) == "admin",
            },
        })

    user_info = get_user_info_by_tg_id(telegram_id) or {}
    first_name = user_info.get("fio", "").split()[0] if user_info.get("fio") else ""

    return jsonify({
        "students": [
            {
                "id": s["id"],
                "fio": s["fio"],
                "display_name": s.get("display_name") or s["fio"],
            }
            for s in students
        ],
        "user": {
            "lang": get_user_lang(telegram_id),
            "first_name": first_name,
            "is_admin": user_info.get("role") == "admin",
        },
    })


# ════════════════════════════════════════════════════════════
#  ROUTES — legacy (обратная совместимость)
# ════════════════════════════════════════════════════════════

@app.route("/api/students")
def api_students():
    """[Legacy] Список учеников. Новый код использует /api/dashboard/init."""
    auth = _get_authenticated_user()
    students = get_students_for_parent(auth["telegram_id"])
    return jsonify([
        {"id": s["id"], "fio": s["fio"], "display_name": s.get("display_name") or s["fio"]}
        for s in students
    ])


@app.route("/api/grades/<int:student_id>")
def api_grades(student_id):
    """[Legacy] Сырые оценки. Новый код использует /api/dashboard."""
    _authorize_student_access(student_id)

    days = request.args.get("days", 30, type=int)
    days = min(days, 365)

    subject = request.args.get("subject", "").strip()
    grades = get_grade_history_for_student_all(student_id, days=days)
    if subject:
        grades = [g for g in grades if g['subject'] == subject]

    return jsonify(grades)


@app.route("/api/quarters/<int:student_id>")
def api_quarters(student_id):
    """Четвертные оценки (lazy-loaded когда юзер раскрывает секцию)."""
    _authorize_student_access(student_id)
    return jsonify(get_quarter_grades(student_id))


@app.route("/health")
def health():
    """Health check для Caddy/мониторинга."""
    return jsonify({"status": "ok"})


# ════════════════════════════════════════════════════════════
#  ENTRYPOINT (только для local dev — на проде gunicorn)
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("WEBAPP_PORT", 8443))
    host = os.environ.get("WEBAPP_HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
