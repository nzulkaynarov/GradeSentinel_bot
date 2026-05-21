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
from datetime import datetime, timedelta, timezone, date
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
from src.db.connection import get_db_connection
from src.i18n import load_translations

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
# При запуске под gunicorn `app.run()` не вызывается, поэтому init_db и
# load_translations нужно дёрнуть здесь. Без load_translations() функция t()
# возвращает сам ключ → AI-prompt-ы получают буквально "insight_prompt"
# вместо текста, и Claude отвечает мета-описанием своих способностей.
init_db()
load_translations()

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

    Группировка по grade_date (фактическая дата оценки), fallback на date_added
    для совместимости со старыми записями где grade_date пока не заполнен.
    """
    by_date = defaultdict(list)
    for g in grades:
        if g.get("grade_value") is None:
            continue
        date_str = g.get("grade_date")
        if not date_str and g.get("date_added"):
            date_str = g["date_added"][:10]
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


_RU_MONTH_NAMES = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}


def _grade_date_str(g) -> str:
    """grade_date с fallback на date_added для legacy записей."""
    gd = g.get("grade_date")
    if gd:
        return gd
    return (g.get("date_added") or "")[:10]


def compute_year_report(grades):
    """Итоги учебного года (для end-of-year dashboard view).

    Возвращает агрегаты по всему учебному году: общий avg, помесячный тренд,
    топ/проблемные предметы, рост/падение, лучшую серию пятёрок.

    Все аргументы — list[dict] с полями subject, grade_value, raw_text, grade_date.
    Pure-функция, идеально для unit-тестов.
    """
    if not grades:
        return {
            "total_grades": 0,
            "numeric_count": 0,
            "year_avg": None,
            "months_active": 0,
            "monthly_trend": [],
            "best_month": None,
            "worst_month": None,
            "top_subjects": [],
            "problem_subjects": [],
            "best_streak": 0,
            "growth": None,
        }

    numeric_grades = [g for g in grades if g.get("grade_value") is not None]
    numeric_count = len(numeric_grades)
    numeric_vals = [g["grade_value"] for g in numeric_grades]

    year_avg = round(sum(numeric_vals) / len(numeric_vals), 2) if numeric_vals else None

    # Помесячный тренд (YYYY-MM)
    by_month = defaultdict(list)
    for g in numeric_grades:
        date_str = _grade_date_str(g)
        if len(date_str) >= 7:
            ym = date_str[:7]  # "2025-09"
            by_month[ym].append(g["grade_value"])

    monthly_trend = []
    for ym in sorted(by_month):
        vals = by_month[ym]
        year = int(ym[:4])
        month = int(ym[5:7])
        monthly_trend.append({
            "month": ym,
            "label": f"{_RU_MONTH_NAMES[month]} {year}",
            "avg": round(sum(vals) / len(vals), 2),
            "count": len(vals),
        })

    best_month = max(monthly_trend, key=lambda m: m["avg"]) if monthly_trend else None
    worst_month = min(monthly_trend, key=lambda m: m["avg"]) if monthly_trend else None

    # Per-subject статистика с минимум 3 оценками для top/problem (иначе одна
    # счастливая 5 попадает в «топ»)
    by_subj = defaultdict(list)
    for g in numeric_grades:
        by_subj[g["subject"]].append(g["grade_value"])

    subject_stats = sorted([
        {
            "name": subj,
            "avg": round(sum(vals) / len(vals), 2),
            "count": len(vals),
        }
        for subj, vals in by_subj.items()
    ], key=lambda s: -s["avg"])

    significant_subjects = [s for s in subject_stats if s["count"] >= 3]
    top_subjects = significant_subjects[:5]
    problem_subjects = sorted(
        [s for s in significant_subjects if s["avg"] <= GRADE_PROBLEM_THRESHOLD],
        key=lambda s: s["avg"],
    )[:5]

    # Лучшая серия пятёрок (грубая: подряд по date_added в хронологии)
    sorted_by_date = sorted(numeric_grades, key=lambda g: (_grade_date_str(g), g.get("date_added") or ""))
    best_streak = 0
    current_streak = 0
    for g in sorted_by_date:
        if g["grade_value"] >= 5:
            current_streak += 1
            if current_streak > best_streak:
                best_streak = current_streak
        else:
            current_streak = 0

    # Рост Q1→Q4: первая четверть учебного года vs последняя.
    # Простая эвристика — первая треть года (по количеству numeric_grades)
    # vs последняя треть. Без quarter_grades — модели работают по grade_date.
    growth = None
    if numeric_count >= 6:
        third = numeric_count // 3
        first_part = sorted_by_date[:third]
        last_part = sorted_by_date[-third:]
        first_avg = sum(g["grade_value"] for g in first_part) / len(first_part)
        last_avg = sum(g["grade_value"] for g in last_part) / len(last_part)
        growth = round(last_avg - first_avg, 2)

    return {
        "total_grades": len(grades),
        "numeric_count": numeric_count,
        "year_avg": year_avg,
        "months_active": len(by_month),
        "monthly_trend": monthly_trend,
        "best_month": best_month,
        "worst_month": worst_month,
        "top_subjects": top_subjects,
        "problem_subjects": problem_subjects,
        "best_streak": best_streak,
        "growth": growth,
    }


# ════════════════════════════════════════════════════════════
#  ROUTES — основные
# ════════════════════════════════════════════════════════════

@app.route("/webapp")
def dashboard():
    """Serves the main dashboard HTML page."""
    return render_template("dashboard.html")


def _dashboard_etag(student_id: int, days: int, telegram_id: int) -> str:
    """ETag для /api/dashboard. Дёшево: SHA1(watermark + 6h-bucket).

    Watermark — MAX(date_added) для оценок этого ученика → меняется при
    любом INSERT/UPDATE через monitor или history_importer.

    6h-bucket совпадает с TTL AI-инсайта (compute_dashboard_insight кэширует
    на 6 часов). Гарантирует что после обновления insight клиент получит
    новый ETag.

    Включаем days и telegram_id — иначе разные клиенты с разными ?days
    или разный lang/first_name получили бы одинаковый ETag.
    """
    from hashlib import sha1
    with get_db_connection() as conn:
        cur = conn.cursor()
        # MAX + COUNT: MAX ловит UPDATE (date_added = CURRENT_TIMESTAMP),
        # COUNT ловит INSERT даже когда несколько вставок в одну секунду
        # (CURRENT_TIMESTAMP в SQLite — секундная точность).
        cur.execute(
            "SELECT MAX(date_added), COUNT(*) FROM grade_history WHERE student_id = ?",
            (student_id,),
        )
        row = cur.fetchone()
        watermark = (row[0] if row and row[0] else "") if row else ""
        count = row[1] if row else 0

    # 6h-bucket в UTC. Сменяется в 0/6/12/18 UTC = 5/11/17/23 TST.
    # Совпадает с TTL AI-инсайта (6h cache) — гарантирует invalidation
    # после обновления insight'а.
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    bucket = now_utc.strftime("%Y%m%d") + str(now_utc.hour // 6)

    src = f"{telegram_id}:{student_id}:{days}:{watermark}:{count}:{bucket}"
    return sha1(src.encode("utf-8")).hexdigest()[:16]


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

    Поддерживает ETag / If-None-Match → 304 Not Modified для экономии трафика
    при повторных открытиях дашборда без новых оценок.
    """
    telegram_id = _authorize_student_access(student_id)

    days = request.args.get("days", 7, type=int)
    days = max(1, min(days, 365))

    # ETag check ДО построения тяжёлого ответа (AI insight + queries).
    etag = _dashboard_etag(student_id, days, telegram_id)
    client_etag = request.headers.get("If-None-Match", "").strip('"')
    if client_etag and client_etag == etag:
        # 304 Not Modified — тело пустое, клиент использует кэшированное
        return ("", 304, {"ETag": f'"{etag}"', "Cache-Control": "private, max-age=0"})

    # Тащим за days*2 чтобы посчитать delta vs предыдущий период
    all_grades = get_grade_history_for_student_all(student_id, days=days * 2)

    # Разделение на current и previous по grade_date (фактической дате оценки).
    # Cutoff — N дней назад от сегодня по Ташкенту (UTC+5), чтобы граница периодов
    # не зависела от часа запроса.
    today_tashkent = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date()
    cutoff_date = (today_tashkent - timedelta(days=days)).isoformat()

    def _grade_date(g):
        gd = g.get("grade_date")
        if gd:
            return gd
        # Fallback для записей которые ещё не получили grade_date через backfill.
        return (g.get("date_added") or "")[:10]

    grades_current = [g for g in all_grades if _grade_date(g) >= cutoff_date]
    grades_previous = [g for g in all_grades if _grade_date(g) < cutoff_date]

    summary = compute_summary(grades_current, grades_previous, days)
    trend_by_day = compute_trend_by_day(grades_current, days)
    by_subject = compute_by_subject(grades_current)

    # User info — для приветствия и определения языка.
    # telegram_first_name пишется в parents при /start — приоритетнее, чем fio
    # (которое часто формальное ФИО или admin-заданное).
    user_info = get_user_info_by_tg_id(telegram_id) or {}
    lang = get_user_lang(telegram_id)
    first_name = user_info.get("telegram_first_name") or ""
    if not first_name and user_info.get("fio"):
        first_name = user_info["fio"].split()[0]

    # Dashboard refresh: убрали AI-инсайт из ответа. AI теперь живёт только
    # в чате (бот). Дашборд — строго данные, родитель сам делает выводы.
    # Функция compute_dashboard_insight сохранена в analytics_engine на случай
    # будущих use cases, но больше не зовётся при каждом открытии (экономит
    # ~$0.001/open на Anthropic API).

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

    response = jsonify(response_data)
    response.headers["ETag"] = f'"{etag}"'
    # private — кэш только в браузере клиента (Caddy/proxy не должны кэшировать
    # под одним ключом для разных пользователей). max-age=0 — клиент должен
    # ревалидировать через If-None-Match.
    response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    return response


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
    first_name = user_info.get("telegram_first_name") or ""
    if not first_name and user_info.get("fio"):
        first_name = user_info["fio"].split()[0]

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
#  ROUTES — end-of-year отчёт (учебный год 2025-09 → 2026-05)
# ════════════════════════════════════════════════════════════

@app.route("/api/dashboard/<int:student_id>/pdf")
def api_dashboard_pdf(student_id):
    """Экспорт дашборда в PDF (Dashboard refresh).

    Использует webapp.pdf_export.build_dashboard_pdf — reportlab + DejaVuSans
    для кириллицы. Возвращает application/pdf с Content-Disposition:
    attachment чтобы браузер/Telegram WebApp скачивали как файл.

    Query params:
      days — длина периода (по умолчанию 30 для PDF чтобы был информативный
             объём, max 365).
    """
    from flask import Response
    from webapp.pdf_export import build_dashboard_pdf

    telegram_id = _authorize_student_access(student_id)

    days = request.args.get("days", 30, type=int)
    days = max(1, min(days, 365))

    students = get_students_for_parent(telegram_id)
    student = next((s for s in students if s["id"] == student_id), None)
    if not student:
        abort(403)
    student_name = student.get("display_name") or student.get("fio") or "ученик"
    lang = get_user_lang(telegram_id)

    all_grades = get_grade_history_for_student_all(student_id, days=days * 2)
    today_tashkent = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date()
    cutoff_date = (today_tashkent - timedelta(days=days)).isoformat()

    def _gd(g):
        return g.get("grade_date") or (g.get("date_added") or "")[:10]

    grades_current = [g for g in all_grades if _gd(g) >= cutoff_date]
    grades_previous = [g for g in all_grades if _gd(g) < cutoff_date]

    summary = compute_summary(grades_current, grades_previous, days)
    by_subject = compute_by_subject(grades_current)

    period_labels = {
        'ru': {7: 'неделя', 14: '2 недели', 30: 'месяц', 90: 'квартал', 365: 'год'},
        'uz': {7: 'hafta', 14: '2 hafta', 30: 'oy', 90: 'chorak', 365: 'yil'},
        'en': {7: 'week', 14: '2 weeks', 30: 'month', 90: 'quarter', 365: 'year'},
    }
    period_label = period_labels.get(lang, period_labels['ru']).get(days, f"{days} дн.")

    pdf_bytes = build_dashboard_pdf(
        student_name=student_name,
        summary=summary,
        by_subject=by_subject,
        recent=grades_current,
        period_label=period_label,
        lang=lang,
    )

    safe_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in student_name)
    filename = f"GradeSentinel_{safe_name}_{today_tashkent.isoformat()}.pdf"

    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Length': str(len(pdf_bytes)),
            'Cache-Control': 'private, no-store',
        },
    )


@app.route("/api/dashboard/year/<int:student_id>")
def api_dashboard_year(student_id):
    """Итоги учебного года для дашборда. Подгружается lazy при клике на
    «Итоги года» (не блокирует основной view).

    Берём все оценки за учебный год: с 1 сентября предыдущего года.
    Используем days=365 — покрывает любой учебный год независимо от того,
    в каком месяце сейчас просматривают."""
    telegram_id = _authorize_student_access(student_id)

    # Все оценки за учебный год. days=365 гарантирует что и в августе,
    # и в мае мы возьмём правильный объём истории.
    all_grades = get_grade_history_for_student_all(student_id, days=365)

    # Отфильтровать на учебный год (с 1 сентября). Если сейчас сентябрь+ —
    # учебный год начался в этом году, иначе — в прошлом.
    today_tashkent = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date()
    if today_tashkent.month >= 9:
        school_year_start = date(today_tashkent.year, 9, 1).isoformat()
    else:
        school_year_start = date(today_tashkent.year - 1, 9, 1).isoformat()

    year_grades = [g for g in all_grades if _grade_date_str_for_filter(g) >= school_year_start]

    report = compute_year_report(year_grades)
    report["school_year_start"] = school_year_start

    # Dashboard refresh: убрали AI годовой инсайт. AI теперь только в чате.

    return jsonify(report)


def _grade_date_str_for_filter(g) -> str:
    """Stable string-comparable date для фильтрации по началу учебного года."""
    return _grade_date_str(g)


# ════════════════════════════════════════════════════════════
#  ROUTES — AI chat
# ════════════════════════════════════════════════════════════

# Простой in-memory rate limit per telegram_id: 5 запросов в минуту.
# При рестарте сбрасывается — допустимо для single-instance.
_chat_rate_limit = defaultdict(list)  # tg_id -> [timestamp, ...]
_CHAT_RATE_LIMIT_MAX = 5
_CHAT_RATE_LIMIT_WINDOW_SEC = 60
_CHAT_MAX_QUESTION_LEN = 500


def _check_chat_rate_limit(telegram_id: int) -> bool:
    """True если можно отправить, False если превышен лимит."""
    import time
    now = time.time()
    history = _chat_rate_limit[telegram_id]
    # Чистим старые
    _chat_rate_limit[telegram_id] = [t for t in history if now - t < _CHAT_RATE_LIMIT_WINDOW_SEC]
    if len(_chat_rate_limit[telegram_id]) >= _CHAT_RATE_LIMIT_MAX:
        return False
    _chat_rate_limit[telegram_id].append(now)
    return True


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """AI-чат с контекстом ученика. Принимает question, возвращает ответ Claude.

    Body: {student_id: int, question: str}
    Auth: X-Telegram-Init-Data header (как все остальные endpoints).
    """
    body = request.get_json(silent=True) or {}
    student_id = body.get("student_id")
    question = (body.get("question") or "").strip()

    if not isinstance(student_id, int) or not question:
        abort(400)
    if len(question) > _CHAT_MAX_QUESTION_LEN:
        abort(400)

    telegram_id = _authorize_student_access(student_id)

    if not _check_chat_rate_limit(telegram_id):
        return ("Rate limit exceeded", 429)

    # NAV-001: pivot на family_id внутри (URL контракт остался student_id
    # для backward compat). Webapp chat теперь shared с bot history,
    # AI видит всех детей семьи и может сравнивать.
    students = get_students_for_parent(telegram_id)
    student = next((s for s in students if s["id"] == student_id), None)
    if not student:
        abort(403)

    from src.database_manager import (
        get_families_for_student, get_family_students,
        get_recent_family_chat_history, save_family_chat_message,
    )
    fams = get_families_for_student(student_id)
    if not fams:
        abort(403)
    family_id = fams[0]['id']
    family_students = get_family_students(family_id)

    # Собираем grades всех детей семьи с annotation
    all_grades = []
    student_names = []
    for s in family_students:
        s_name = s.get("display_name") or s.get("fio") or "ученик"
        student_names.append(s_name)
        s_grades = get_grade_history_for_student_all(s['id'], days=365)
        for g in s_grades:
            gg = dict(g)
            gg['student_name'] = s_name
            all_grades.append(gg)
    all_grades.sort(
        key=lambda g: g.get('grade_date') or (g.get('date_added') or '')[:10],
        reverse=True,
    )
    family_label = student_names[0] if len(student_names) == 1 else ", ".join(student_names)
    lang = get_user_lang(telegram_id)

    # Multi-turn history (family-scoped после NAV-001)
    prev_messages = get_recent_family_chat_history(telegram_id, family_id)

    # Save user message before AI call (orphan if AI fails)
    save_family_chat_message(telegram_id, family_id, 'user', question)

    try:
        from src.analytics_engine import answer_parent_question
        answer = answer_parent_question(
            student_id=None,
            student_name=family_label,
            grades=all_grades,
            question=question,
            lang=lang,
            prev_messages=prev_messages,
            family_id=family_id,
        )
    except Exception as e:
        logger.warning(f"Chat error for tg={telegram_id} family={family_id}: {e}")
        return jsonify({"answer": None, "error": "internal"}), 500

    if not answer:
        return jsonify({"answer": None, "error": "no_response"}), 503

    assistant_msg_id = save_family_chat_message(telegram_id, family_id, 'assistant', answer)
    return jsonify({"answer": answer, "message_id": assistant_msg_id})


@app.route("/api/chat/history/<int:student_id>")
def api_chat_history(student_id):
    """Возвращает chat-сообщения для рендера в dashboard chat-section.

    NAV-001: внутри pivot на family_id (student_id из URL → resolve семью).
    URL контракт остался для backward compat фронта."""
    telegram_id = _authorize_student_access(student_id)
    from src.database_manager import get_families_for_student, get_recent_family_chat_history
    fams = get_families_for_student(student_id)
    if not fams:
        return jsonify({"messages": []})
    history = get_recent_family_chat_history(telegram_id, fams[0]['id'])
    return jsonify({"messages": history})


@app.route("/api/chat/clear/<int:student_id>", methods=["POST"])
def api_chat_clear(student_id):
    """Очищает family-scoped историю чата (NAV-001: pivot на family_id)."""
    telegram_id = _authorize_student_access(student_id)
    from src.database_manager import get_families_for_student, clear_family_chat_history
    fams = get_families_for_student(student_id)
    if fams:
        clear_family_chat_history(telegram_id, fams[0]['id'])
    return jsonify({"ok": True})


@app.route("/api/chat/feedback", methods=["POST"])
def api_chat_feedback():
    """PR_H3: 👍/👎 на конкретный AI ответ.

    Body: {message_id: int, rating: 1 | -1, comment?: str}
    Авторизация: message должно принадлежать вызывающему telegram_id.
    UPSERT — повторный POST с другим rating заменяет предыдущий."""
    auth = _get_authenticated_user()
    telegram_id = auth["telegram_id"]

    payload = request.get_json(silent=True) or {}
    try:
        message_id = int(payload.get("message_id"))
        rating = int(payload.get("rating"))
    except (TypeError, ValueError):
        abort(400)
    comment = payload.get("comment")
    if comment is not None and not isinstance(comment, str):
        abort(400)
    if comment and len(comment) > 500:
        abort(400)
    if rating not in (1, -1):
        abort(400)

    from src.database_manager import get_message_owner, save_feedback
    owner = get_message_owner(message_id)
    if owner is None:
        abort(404)
    if owner != telegram_id:
        # Не палим разницу 403/404 чтобы не утечка существования чужих msg_id
        abort(404)

    save_feedback(message_id, telegram_id, rating, comment)
    return jsonify({"ok": True})


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
