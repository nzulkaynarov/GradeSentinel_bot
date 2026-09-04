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
import time
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
    get_student_academic_year,
)
from src.history_importer import current_academic_year, is_sheet_stale
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
# Статика версионируется через ?v={{ build_id }} (см. dashboard.html), поэтому
# её можно кэшировать надолго. По умолчанию Flask отдаёт Cache-Control: no-cache
# и браузер ревалидировал КАЖДЫЙ файл при каждом открытии: четыре лишних
# round-trip (~480 мс на мобильной сети) без единого байта полезных данных.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# S2: TTL свежести initData. Валидный HMAC вечен, поэтому утёкшая ссылка на
# дашборд без TTL = бессрочный доступ к детской PII. Telegram кладёт auth_date
# (unix ts) в initData; принимаем подпись не старше суток.
INIT_DATA_MAX_AGE = 24 * 60 * 60          # 24 часа
INIT_DATA_CLOCK_SKEW = 5 * 60             # допуск на рассинхрон часов (5 мин в будущее)


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

    # S2: подпись верна — теперь проверяем свежесть auth_date (replay-защита).
    auth_date_raw = parsed.get("auth_date", [None])[0]
    if not auth_date_raw:
        raise ValueError("No auth_date in initData")
    try:
        auth_date = int(auth_date_raw)
    except (ValueError, TypeError):
        raise ValueError("Invalid auth_date")
    age = time.time() - auth_date
    if age > INIT_DATA_MAX_AGE:
        raise ValueError("initData expired")
    if age < -INIT_DATA_CLOCK_SKEW:
        # auth_date заметно в будущем — подделка/битые часы клиента.
        raise ValueError("auth_date in future")

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
MIN_SAMPLE = 3
# Меньше этого числа оценок — это шум, а не сигнал. Один и тот же порог для
# дельты периода и для «проблемных предметов»: раньше KPI-карточки честно писали
# «недостаточно данных» (KPI_MIN_SAMPLE=3), а статус-строка рядом уже тревожила
# из-за единственной двойки, и дельта показывала −1.49 против ОДНОЙ оценки
# в предыдущем периоде (аудит 2026-09-03).


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
    # Сравнивать периоды имеет смысл, только если в обоих есть выборка. Иначе
    # «год против одной сентябрьской оценки» рисует родителю падение на 1.5 балла.
    if (avg_current is not None and avg_previous is not None
            and len(numeric_current) >= MIN_SAMPLE and len(numeric_previous) >= MIN_SAMPLE):
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

    # Проблемные = avg <= 3.5 при достаточной выборке (худшие первые).
    # Без порога единственная двойка за период включала статус «есть на что
    # обратить внимание», пока KPI на том же экране писал «недостаточно данных».
    problem_subjects = sorted(
        [s for s in subject_stats
         if s["avg"] <= GRADE_PROBLEM_THRESHOLD and s["count"] >= MIN_SAMPLE],
        key=lambda s: s["avg"],
    )[:5]

    # Топовые = avg >= 4.5, тот же порог выборки
    top_subjects = sorted(
        [s for s in subject_stats
         if s["avg"] >= GRADE_GOOD_THRESHOLD and s["count"] >= MIN_SAMPLE],
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
        # psycopg возвращает date/datetime ОБЪЕКТЫ для date/timestamp-колонок —
        # _grade_date_str нормализует всё в строку 'YYYY-MM-DD' (стабильный
        # JSON-ключ + сортируемость).
        date_str = _grade_date_str(g)
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
    """Разбивка по предметам, отсортированная по среднему DESC.

    Dashboard refactor: enriched данные — last grade (с датой), тренд
    (delta vs first-half period vs second-half), и raw values для UI.
    """
    by_subj = defaultdict(list)
    for g in grades:
        if g.get("grade_value") is None:
            continue
        by_subj[g["subject"]].append({
            'value': g["grade_value"],
            'raw_text': g.get("raw_text", ""),
            'date': _grade_date_str(g),
        })

    out = []
    for subj, entries in by_subj.items():
        # Sort by date ASC for trend computation
        entries_sorted = sorted(entries, key=lambda e: e['date'])
        values = [e['value'] for e in entries_sorted]
        avg = round(sum(values) / len(values), 2)
        last = entries_sorted[-1]

        # Trend: средний первой половины vs второй половины. Если перепад
        # ≥ 0.3 — показываем стрелку. Это устойчивее чем «последняя оценка».
        trend = 'flat'
        if len(values) >= 4:
            half = len(values) // 2
            avg_early = sum(values[:half]) / half
            avg_late = sum(values[half:]) / (len(values) - half)
            if avg_late - avg_early >= 0.3:
                trend = 'up'
            elif avg_early - avg_late >= 0.3:
                trend = 'down'

        out.append({
            "name": subj,
            "avg": avg,
            "count": len(values),
            "last_grade": last['raw_text'],
            "last_date": last['date'],
            "trend": trend,
        })
    return sorted(out, key=lambda s: -s["avg"])


def compute_trend_by_subject(grades, period_days, max_subjects=8):
    """Multi-line chart данные: по неделе для каждого предмета.

    Dashboard refactor: заменяет старый compute_trend_by_day (который был
    шумом — «средний по всем предметам за день»). Теперь видно «физика
    проседает», «литература стабильна».

    Группировка по неделе (а не дню) — снижает шум, читаемее.
    Top N предметов по кол-ву оценок чтобы не перегрузить chart.
    """
    by_subj = defaultdict(lambda: defaultdict(list))
    for g in grades:
        if g.get("grade_value") is None:
            continue
        date_str = _grade_date_str(g)
        if not date_str:
            continue
        try:
            d = datetime.fromisoformat(date_str).date()
        except (ValueError, TypeError):
            continue
        # Week bucket — понедельник этой недели (ISO weekday)
        week_start = (d - timedelta(days=d.weekday())).isoformat()
        by_subj[g["subject"]][week_start].append(g["grade_value"])

    # Топ N предметов по кол-ву оценок (то что чаще встречается)
    top_subjects = sorted(
        by_subj.items(),
        key=lambda kv: -sum(len(v) for v in kv[1].values()),
    )[:max_subjects]

    result = []
    for subj, weeks in top_subjects:
        points = []
        for week, vals in sorted(weeks.items()):
            points.append({
                "week": week,
                "avg": round(sum(vals) / len(vals), 2),
                "count": len(vals),
            })
        result.append({"subject": subj, "points": points})
    return result


def compute_quarters_with_forecast(quarter_grades):
    """Группировка четвертных по предмету + прогноз годовой.

    Dashboard refactor: в БД quarter=5 = годовая (см. CLAUDE.md schema).
    Если строка q=5 есть для предмета — используем её. Если нет — простой
    forecast = среднее имеющихся четвертей (с weight на более свежие если
    хватит данных).

    Возвращает [{subject, q1, q2, q3, q4, year, year_is_forecast, trend}]
    отсортировано по подходу: проблемные сверху если year < 4.
    """
    by_subj = defaultdict(dict)
    for qg in quarter_grades:
        subj = qg.get("subject", "?")
        q = qg.get("quarter")
        if q not in (1, 2, 3, 4, 5):
            continue
        by_subj[subj][q] = {
            "raw": qg.get("raw_text", ""),
            "value": qg.get("grade_value"),
        }

    result = []
    for subj, quarters in by_subj.items():
        row = {
            "subject": subj,
            "q1": quarters.get(1, {}).get("raw"),
            "q2": quarters.get(2, {}).get("raw"),
            "q3": quarters.get(3, {}).get("raw"),
            "q4": quarters.get(4, {}).get("raw"),
            "year": None,
            "year_is_forecast": False,
            "year_value": None,
        }
        # Year — либо явная (q=5), либо прогноз
        if 5 in quarters and quarters[5].get("value") is not None:
            row["year"] = quarters[5]["raw"]
            row["year_value"] = quarters[5]["value"]
        else:
            # Прогноз: среднее ненулевых четвертных. Если есть только 1-2
            # четверти — указываем low confidence через is_forecast=True.
            numeric_quarters = [
                quarters[q]["value"] for q in (1, 2, 3, 4)
                if q in quarters and quarters[q].get("value") is not None
            ]
            if numeric_quarters:
                forecast = sum(numeric_quarters) / len(numeric_quarters)
                row["year"] = f"~{forecast:.1f}"
                row["year_value"] = round(forecast, 2)
                row["year_is_forecast"] = True

        # Trend: q1 → q4 (или последняя имеющаяся)
        ordered = [
            quarters[q]["value"] for q in (1, 2, 3, 4)
            if q in quarters and quarters[q].get("value") is not None
        ]
        if len(ordered) >= 2:
            if ordered[-1] - ordered[0] >= 0.5:
                row["trend"] = "up"
            elif ordered[0] - ordered[-1] >= 0.5:
                row["trend"] = "down"
            else:
                row["trend"] = "flat"
        else:
            row["trend"] = "flat"

        result.append(row)

    # Сортировка: проблемные сверху (year_value < 4 или is_forecast и low),
    # внутри группы — alphabet
    def sort_key(r):
        is_problem = r["year_value"] is not None and r["year_value"] < 4
        return (not is_problem, r["subject"])
    return sorted(result, key=sort_key)


KPI_MIN_SAMPLE = MIN_SAMPLE  # исторический алиас; порог один на весь дашборд


def compute_dashboard_kpis(summary, by_subject, total_grades_count):
    """4 KPI cards для верха дашборда. Простой derive из summary +
    by_subject (которые уже есть).

    Honesty fix: top/worst игнорируют предметы с count < KPI_MIN_SAMPLE,
    чтобы не показывать «Лучший: Анатомия 5.0» по 1 случайной пятёрке.
    Count возвращается во фронт чтобы UI мог показать «(N оценок)»."""
    eligible = [s for s in by_subject if s.get("count", 0) >= KPI_MIN_SAMPLE]
    top = max(eligible, key=lambda s: s["avg"], default=None) if eligible else None
    worst = min(eligible, key=lambda s: s["avg"], default=None) if eligible else None

    def _shape(s):
        if not s:
            return None
        return {"name": s["name"], "avg": s["avg"], "count": s.get("count", 0)}

    return {
        "current_avg": summary.get("current_avg"),
        "delta": summary.get("delta"),
        "total_grades": total_grades_count,
        "period_days": summary.get("period_days"),
        "top_subject": _shape(top),
        "worst_subject": _shape(worst),
    }


_RU_MONTH_NAMES = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}


def _iso(value) -> str:
    """Нормализует значение date/timestamp-колонки в строку.

    psycopg v3 возвращает python date/datetime ОБЪЕКТЫ (а не строки, как
    делал sqlite3). Любой код, который ниже делает строковые операции над
    датами ([:10], [:7], fromisoformat, строковые сравнения >=/<=), должен
    получать стабильную ISO-строку. date/datetime → .isoformat(); строки
    (legacy/тестовые данные) и прочее → str(); None/пусто → ''.
    """
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _grade_date_str(g) -> str:
    """grade_date с fallback на date_added для legacy записей.

    Всегда возвращает строку 'YYYY-MM-DD' (через _iso), даже когда psycopg
    отдал date/datetime ОБЪЕКТ — иначе строковые сравнения/срезы ниже падают.
    """
    gd = g.get("grade_date")
    if gd:
        # grade_date — DATE-колонка → isoformat() даёт ровно 'YYYY-MM-DD'.
        return _iso(gd)
    # date_added — TIMESTAMP → isoformat() = 'YYYY-MM-DDT...', берём дату.
    return _iso(g.get("date_added"))[:10]


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
    sorted_by_date = sorted(numeric_grades, key=lambda g: (_grade_date_str(g), _iso(g.get("date_added"))))
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

_BUILD_ID = str(int(datetime.now().timestamp()))


@app.route("/webapp")
def dashboard():
    """Serves the main dashboard HTML page.

    Embed bot_username в HTML чтобы JS не зависел от /api/dashboard/init.
    build_id (= webapp boot timestamp) добавляется в URL'ы app.js/style.css/
    locales для cache-busting — Telegram WebView aggressively cache'ит
    статику, после deploy юзер получал старый JS без новых ключей/handler'ов
    (AI кнопка молчала)."""
    html = render_template(
        "dashboard.html",
        bot_username=_get_bot_username() or "",
        build_id=_BUILD_ID,
    )
    # HTML несёт в себе build_id, которым версионируется вся статика. Если
    # закэшируется он сам — версия замрёт, и cache-busting перестанет работать
    # (Telegram WebView кэширует такие ответы эвристически).
    return html, 200, {"Cache-Control": "no-store, must-revalidate"}


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
            "SELECT MAX(date_added), COUNT(*) FROM grade_history WHERE student_id = %s",
            (student_id,),
        )
        row = cur.fetchone()
        # MAX(date_added) — psycopg отдаёт datetime ОБЪЕКТ; нормализуем в строку
        # чтобы cache-key (ниже) был стабилен.
        watermark = _iso(row[0]) if row else ""
        count = row[1] if row else 0

        # Четвертные — отдельная таблица и отдельный блок ответа. Без них правка
        # четвертной оценки не меняла ETag, и родитель до шести часов получал 304
        # со старой карточкой (аудит 2026-09-03).
        cur.execute(
            "SELECT MAX(updated_at), COUNT(*) FROM quarter_grades WHERE student_id = %s",
            (student_id,),
        )
        qrow = cur.fetchone()
        q_watermark = f"{_iso(qrow[0]) if qrow else ''}:{qrow[1] if qrow else 0}"

        # Смена таблицы или учебного года меняет смысл ответа, не трогая оценки.
        cur.execute(
            "SELECT spreadsheet_id, academic_year, display_name FROM students WHERE id = %s",
            (student_id,),
        )
        srow = cur.fetchone()
        student_state = f"{srow[0]}:{srow[1]}:{srow[2]}" if srow else ""

        # Язык и имя попадают в тело ответа — значит и в ключ кэша.
        cur.execute(
            "SELECT lang, telegram_first_name FROM parents WHERE telegram_id = %s",
            (telegram_id,),
        )
        prow = cur.fetchone()
        user_state = f"{prow[0]}:{prow[1]}" if prow else ""

    # Бакет считаем по ТАШКЕНТУ, а не по UTC. Границы периодов режутся по
    # ташкентской дате, поэтому UTC-бакет между 00:00 и 05:00 местного времени
    # отдавал 304 со вчерашним окном: вчерашняя группа «Сегодня», вчерашние
    # period_start/period_end (аудит 2026-09-03).
    now_tashkent = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)
    bucket = now_tashkent.strftime("%Y%m%d") + str(now_tashkent.hour // 6)

    src = (f"{telegram_id}:{student_id}:{days}:{watermark}:{count}:"
           f"{q_watermark}:{student_state}:{user_state}:{bucket}")
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

    # _grade_date_str всегда отдаёт строку 'YYYY-MM-DD' (нормализует date/
    # datetime ОБЪЕКТЫ из psycopg) — сравнение с cutoff_date (тоже строка) валидно.
    grades_current = [g for g in all_grades if _grade_date_str(g) >= cutoff_date]
    grades_previous = [g for g in all_grades if _grade_date_str(g) < cutoff_date]

    summary = compute_summary(grades_current, grades_previous, days)
    by_subject = compute_by_subject(grades_current)
    # Dashboard refactor: trend by SUBJECT (multi-line) вместо trend by DAY
    # (был просто шум — средний по всем предметам за день).
    trend_by_subject = compute_trend_by_subject(grades_current, days)
    # KPI cards — derived из summary + by_subject (без extra queries).
    kpis = compute_dashboard_kpis(summary, by_subject, len(grades_current))
    # Четвертные с прогнозом годовой — primary блок (не collapsible).
    from src.database_manager import get_quarter_grades
    # Четвертные — строго за учебный год привязанной таблицы. Иначе на экране
    # оказывалась смесь лет без единой пометки, и родитель читал прошлогодние
    # четверти как текущие (аудит 2026-09-03).
    student_academic_year = get_student_academic_year(student_id)
    quarter_year = student_academic_year or current_academic_year()
    quarter_grades = get_quarter_grades(student_id, academic_year=quarter_year)
    quarters_with_forecast = compute_quarters_with_forecast(quarter_grades)
    # trend_by_day удалён 2026-09-03: поле отдавалось «на пару дней» с 21 мая,
    # во фронте не используется ни разу (grep по app.js), но считалось на каждый
    # запрос и занимало до 1.5 КБ gzip в ответе при days=365.

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
        "kpis": kpis,
        "trend_by_subject": trend_by_subject,
        "by_subject": by_subject,
        "quarters_with_forecast": quarters_with_forecast,
        # Источник на паузе: данные физически не могут обновиться, пока родитель
        # не пришлёт новую ссылку. Экран обязан это показать.
        "sheet_stale": is_sheet_stale(student_academic_year),
        "academic_year": student_academic_year,
        "quarters_academic_year": quarter_year,
        "quarters_academic_year_label": f"{quarter_year}/{str(quarter_year + 1)[-2:]}",
        # Даты нормализуем в ISO ЯВНО. Flask 3 сериализует date/datetime через
        # http_date() → «Wed, 02 Sep 2026 00:00:00 GMT», а фронт сравнивает и
        # режет эти строки как 'YYYY-MM-DD'. Из-за этого группы дат сортировались
        # по названию дня недели, «Сегодня» оказывалось над вчерашними оценками,
        # а подписи оси графика выглядели как «02 Se» (аудит 2026-09-03).
        # cell_reference — debug-метаданные, фронту не нужны.
        "recent_grades": _serialize_grades(grades_current[:100]),
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


_BOT_USERNAME_CACHE = None


def _get_bot_username():
    """Lazy-cached bot username для AI deep-link'ов в frontend.
    Один get_me() при первом вызове, потом из памяти. Fallback на None
    если бот недоступен — frontend проверяет и не показывает deep-link."""
    global _BOT_USERNAME_CACHE
    if _BOT_USERNAME_CACHE is not None:
        return _BOT_USERNAME_CACHE
    bot = _get_webapp_bot()
    if not bot:
        return None
    try:
        _BOT_USERNAME_CACHE = bot.get_me().username
        logger.info(f"Cached bot username: {_BOT_USERNAME_CACHE}")
    except Exception as e:
        logger.warning(f"Failed to fetch bot username: {e}")
        return None
    return _BOT_USERNAME_CACHE


@app.route("/api/dashboard/init")
def api_dashboard_init():
    """
    Bootstrap endpoint: список студентов + язык юзера + имя + bot_username
    для AI deep-link'ов.
    """
    auth = _get_authenticated_user()
    telegram_id = auth["telegram_id"]

    students = get_students_for_parent(telegram_id)
    bot_username = _get_bot_username()
    if not students:
        return jsonify({
            "students": [],
            "user": {
                "lang": get_user_lang(telegram_id),
                "first_name": "",
                "is_admin": get_parent_role(telegram_id) == "admin",
            },
            "bot_username": bot_username,
        })

    user_info = get_user_info_by_tg_id(telegram_id) or {}
    first_name = user_info.get("telegram_first_name") or ""
    if not first_name and user_info.get("fio"):
        first_name = user_info["fio"].split()[0]

    # Состояние источника по каждому ребёнку. Без него дашборд не отличает
    # «новых оценок нет» от «бот перестал читать таблицу»: монитор ставит
    # прошлогоднюю таблицу на паузу и шлёт напоминание в чат, а на экране
    # остаётся замерший прошлогодний срез без единого признака (аудит 2026-09-03).
    current_year = current_academic_year()
    return jsonify({
        "students": [
            {
                "id": s["id"],
                "fio": s["fio"],
                "display_name": s.get("display_name") or s["fio"],
                "academic_year": s.get("academic_year"),
                "sheet_stale": is_sheet_stale(s.get("academic_year")),
            }
            for s in students
        ],
        "current_academic_year": current_year,
        "current_academic_year_label": f"{current_year}/{str(current_year + 1)[-2:]}",
        "bot_username": bot_username,
        "user": {
            "lang": get_user_lang(telegram_id),
            "first_name": first_name,
            "is_admin": user_info.get("role") == "admin",
        },
    })


# ════════════════════════════════════════════════════════════
#  ROUTES — end-of-year отчёт (учебный год 2025-09 → 2026-05)
# ════════════════════════════════════════════════════════════

def _generate_dashboard_pdf(student_id: int, telegram_id: int, days: int,
                              report_type: str = 'full', subject_filter: str = '',
                              date_from: str = '', date_to: str = ''):
    """Общая логика: собирает данные и генерит PDF bytes + filename.
    Используется обоими endpoint'ами (GET download + POST send-to-bot).

    Dashboard refactor v2: фильтры типа отчёта (popup в UI):
      - 'full' — все предметы за `days` (default behavior)
      - 'subject' — только subject_filter
      - 'teacher_talk' — фокус на problem subjects (avg <= 3.5)
      - custom: date_from/date_to override days

    PDF теперь proof-документ — quarters + class + period range + full history."""
    from webapp.pdf_export import build_dashboard_pdf
    from src.database_manager import get_quarter_grades

    days = max(1, min(days, 365))
    students = get_students_for_parent(telegram_id)
    student = next((s for s in students if s["id"] == student_id), None)
    if not student:
        abort(403)
    student_name = student.get("display_name") or student.get("fio") or "ученик"
    # Класс — часто часть display_name типа "Заур (8 Orion)" — для PDF
    # вытаскиваем в отдельное поле если можем; иначе пусто.
    student_class = ''
    if '(' in student_name and ')' in student_name:
        try:
            student_class = student_name[student_name.find('(') + 1:student_name.rfind(')')]
        except Exception:
            student_class = ''
    lang = get_user_lang(telegram_id)

    all_grades = get_grade_history_for_student_all(student_id, days=days * 2)
    today_tashkent = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date()
    cutoff_date = (today_tashkent - timedelta(days=days)).isoformat()
    period_end = today_tashkent.isoformat()

    # Custom period override
    if date_from and date_to:
        cutoff_date = date_from
        period_end = date_to

    # _grade_date_str нормализует date/datetime ОБЪЕКТЫ из psycopg в строку
    # 'YYYY-MM-DD' — сравнения с cutoff_date/period_end (строки) валидны.
    grades_current = [g for g in all_grades if cutoff_date <= _grade_date_str(g) <= period_end]
    grades_previous = [g for g in all_grades if _grade_date_str(g) < cutoff_date]

    # Type-specific фильтрация
    if report_type == 'subject' and subject_filter:
        grades_current = [g for g in grades_current if g.get('subject') == subject_filter]
        grades_previous = [g for g in grades_previous if g.get('subject') == subject_filter]
    # 'teacher_talk' — оставляем все grades, но subjects будут отфильтрованы ниже

    summary = compute_summary(grades_current, grades_previous, days)
    by_subject = compute_by_subject(grades_current)
    # PDF позиционируется как proof-документ для учителя — четвертные в нём
    # должны быть за тот же учебный год, а не за прошлый.
    pdf_quarter_year = get_student_academic_year(student_id) or current_academic_year()
    quarter_grades = get_quarter_grades(student_id, academic_year=pdf_quarter_year)
    quarters = compute_quarters_with_forecast(quarter_grades)

    # 'teacher_talk' — оставляем только problem subjects + четверти по ним
    if report_type == 'teacher_talk':
        problem_names = {s['name'] for s in by_subject if s['avg'] <= 3.5}
        if problem_names:
            by_subject = [s for s in by_subject if s['name'] in problem_names]
            quarters = [q for q in quarters if q['subject'] in problem_names]
            grades_current = [g for g in grades_current if g.get('subject') in problem_names]
    elif report_type == 'subject' and subject_filter:
        quarters = [q for q in quarters if q['subject'] == subject_filter]

    period_labels = {
        'ru': {7: 'неделя', 14: '2 недели', 30: 'месяц', 90: 'квартал', 365: 'год'},
        'uz': {7: 'hafta', 14: '2 hafta', 30: 'oy', 90: 'chorak', 365: 'yil'},
        'en': {7: 'week', 14: '2 weeks', 30: 'month', 90: 'quarter', 365: 'year'},
    }
    period_label = period_labels.get(lang, period_labels['ru']).get(days, f"{days} дн.")

    # Modify period_label для type-specific reports
    if report_type == 'subject' and subject_filter:
        period_label = f"{subject_filter} · {period_label}"
    elif report_type == 'teacher_talk':
        period_label = f"{period_label} · {('фокус: проблемные' if lang=='ru' else 'focus: problems')}"

    pdf_bytes = build_dashboard_pdf(
        student_name=student_name, summary=summary, by_subject=by_subject,
        recent=grades_current, period_label=period_label, lang=lang,
        student_class=student_class,
        quarters=quarters,
        period_start=cutoff_date,
        period_end=period_end,
    )

    full_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in student_name)
    suffix = f"_{report_type}" if report_type != 'full' else ''
    filename = f"GradeSentinel_{full_name}_{today_tashkent.isoformat()}{suffix}.pdf"
    return pdf_bytes, filename, student_name, period_label, lang


# Отдельный TeleBot instance внутри webapp процесса. Bot polling крутится
# в другом процессе (gradesentinel-bot.service); этот instance используется
# только для send_document через Bot API. Lazy init чтобы тесты без BOT_TOKEN
# не падали на module load.
_webapp_bot = None


def _get_webapp_bot():
    global _webapp_bot
    if _webapp_bot is not None:
        return _webapp_bot
    import os as _os
    import telebot
    token = _os.environ.get("BOT_TOKEN")
    if not token or ":" not in token:
        return None
    _webapp_bot = telebot.TeleBot(token)
    return _webapp_bot


@app.route("/api/dashboard/<int:student_id>/pdf/send", methods=["POST"])
def api_dashboard_pdf_send(student_id):
    """Send-to-bot вариант экспорта PDF (Dashboard refresh).

    Проблема blob: URL в Telegram WebView (показывает «Открыть blob://?»
    вместо скачивания файла) → пробуем доставлять PDF как обычный документ
    через bot.send_document(user_id, ...). Юзер видит файл в чате с ботом
    и может сохранять/пересылать стандартными Telegram-механизмами.

    Query params: days (default 30, max 365).
    """
    import io as _io
    telegram_id = _authorize_student_access(student_id)
    days = request.args.get("days", 30, type=int)

    pdf_bytes, filename, student_name, period_label, lang = _generate_dashboard_pdf(
        student_id, telegram_id, days,
        report_type=request.args.get("type", "full"),
        subject_filter=request.args.get("subject", ""),
        date_from=request.args.get("from", ""),
        date_to=request.args.get("to", ""),
    )

    bot = _get_webapp_bot()
    if not bot:
        logger.warning("webapp_bot not available (BOT_TOKEN missing)")
        return jsonify({"error": "bot_unavailable"}), 503

    caption_by_lang = {
        'ru': f"📊 Дашборд: {student_name}\nПериод: {period_label}",
        'uz': f"📊 Panel: {student_name}\nDavr: {period_label}",
        'en': f"📊 Dashboard: {student_name}\nPeriod: {period_label}",
    }
    caption = caption_by_lang.get(lang, caption_by_lang['ru'])

    try:
        # pyTelegramBotAPI принимает file-like объект с .name атрибутом
        # для filename в Telegram.
        f = _io.BytesIO(pdf_bytes)
        f.name = filename
        bot.send_document(telegram_id, f, caption=caption, visible_file_name=filename)
    except Exception as e:
        logger.warning(f"PDF send_document failed for tg={telegram_id}: {e}")
        return jsonify({"error": "send_failed"}), 500

    return jsonify({"ok": True})


@app.route("/api/dashboard/<int:student_id>/pdf")
def api_dashboard_pdf(student_id):
    """GET-вариант экспорта PDF — для случая открытия дашборда в desktop
    браузере (не WebApp). Внутри Telegram WebView лучше использовать
    /pdf/send (POST), потому что WebView не умеет blob: download.

    Query params: days (default 30, max 365).
    """
    from flask import Response
    from urllib.parse import quote

    telegram_id = _authorize_student_access(student_id)
    days = request.args.get("days", 30, type=int)

    pdf_bytes, filename, _name, _period, _lang = _generate_dashboard_pdf(
        student_id, telegram_id, days,
    )

    # Content-Disposition filename должен быть ASCII (RFC 7230). Двойной
    # filename (RFC 6266): ASCII fallback + filename*=UTF-8 для современных
    # клиентов которые покажут юзеру кириллическое имя.
    ascii_name = ''.join(
        c if (c.isascii() and (c.isalnum() or c in '-_')) else '_' for c in filename
    ) or 'GradeSentinel_report.pdf'
    utf8_encoded = quote(filename)

    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={
            'Content-Disposition': (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{utf8_encoded}"
            ),
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

    # Берём с запасом на два учебных года: в начале сентября итоги ТЕКУЩЕГО
    # года ещё пусты, и показывать надо прошедший.
    all_grades = get_grade_history_for_student_all(student_id, days=730)

    today_tashkent = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date()

    # Учебный год берём из students.academic_year — того же поля, по которому
    # монитор решает, актуальна ли таблица. Вычислять его от текущей даты нельзя:
    # именно так «Итоги года» 1 сентября обнулялись и показывали итог трёх дней
    # (а 2026-09-03 — итог трёх ошибочных записей), пряча реальный прошедший год.
    academic_year = get_student_academic_year(student_id)
    if academic_year is None:
        academic_year = current_academic_year(today_tashkent)

    def _slice(year):
        start = date(year, 9, 1).isoformat()
        end = date(year + 1, 8, 31).isoformat()
        return [g for g in all_grades
                if start <= _grade_date_str_for_filter(g) <= end]

    year_grades = _slice(academic_year)
    # Если в «своём» году оценок ещё нет (первые дни сентября), показываем
    # прошедший — он у родителя и есть предмет интереса. Год всегда подписан.
    if not year_grades and academic_year > 2000:
        previous = _slice(academic_year - 1)
        if previous:
            academic_year -= 1
            year_grades = previous

    report = compute_year_report(year_grades)
    report["school_year_start"] = date(academic_year, 9, 1).isoformat()
    report["academic_year"] = academic_year
    report["academic_year_label"] = f"{academic_year}/{str(academic_year + 1)[-2:]}"

    # Dashboard refresh: убрали AI годовой инсайт. AI теперь только в чате.

    return jsonify(report)


def _serialize_grades(grades):
    """Оценки для JSON-ответа: даты строго ISO, только нужные фронту поля."""
    return [
        {
            "subject": g.get("subject"),
            "grade_value": g.get("grade_value"),
            "raw_text": g.get("raw_text"),
            "grade_date": _grade_date_str(g),
            "date_added": _iso(g.get("date_added")),
        }
        for g in grades
    ]


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
    # _grade_date_str нормализует date/datetime ОБЪЕКТЫ (psycopg) в строку —
    # иначе сортировка падала бы на смеси date-объектов и строк / срезе datetime.
    all_grades.sort(key=_grade_date_str, reverse=True)
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
    """Четвертные оценки (lazy-loaded когда юзер раскрывает секцию).

    ?year=YYYY — конкретный учебный год, ?year=all — все. По умолчанию год
    привязанной таблицы."""
    from src.database_manager import ALL_ACADEMIC_YEARS, get_quarter_academic_years

    _authorize_student_access(student_id)
    requested = request.args.get("year", "").strip().lower()
    if requested == "all":
        rows = get_quarter_grades(student_id, academic_year=ALL_ACADEMIC_YEARS)
        year = None
    else:
        year = int(requested) if requested.isdigit() else (
            get_student_academic_year(student_id) or current_academic_year())
        rows = get_quarter_grades(student_id, academic_year=year)
    return jsonify({
        "quarters": rows,
        "academic_year": year,
        "available_years": get_quarter_academic_years(student_id),
    })


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
