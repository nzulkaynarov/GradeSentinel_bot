"""Регрессия: дубли в grade_history из-за race между monitor (двухфазное
подтверждение) и _maybe_sync_all_grades (history-sync в том же main loop).

Сценарий из прода 13 мая 2026 (см. дубли 1094 / 1101):
  19:41:00  monitor видит «2» → «2/5» в Сегодня → PENDING (откладывает UPDATE)
  19:41:02  history-sync читает «Все оценки» (там «2/5» уже стоит на сегодня) →
            SELECT-дедуп ищет raw_text='2/5' в БД, но там пока «2» → не находит
            → INSERT новой строки (Все оценки!IU5)
  19:47:10  monitor подтверждает → UPDATE 1094 на '2/5'
  ⇒ две записи (subject, day, raw_text) одинаковые ⇒ родитель видит дубль.

Фикс: history-sync игнорирует записи с датой == «сегодня по Ташкенту» —
эта зона ответственности monitor'а.
"""
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm
from src.history_importer import import_history_for_student, _parse_all_grades_sheet


def _tashkent_today():
    return (datetime.utcnow() + timedelta(hours=5)).date()


def _date_label_today_ru():
    """«13 мая» — строка, которую парсер дат поймёт как сегодняшнюю в TZ Ташкент."""
    today = _tashkent_today()
    months_ru = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
        5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
        9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря',
    }
    return f"{today.day} {months_ru[today.month]}"


def _date_label_yesterday_ru():
    yday = _tashkent_today() - timedelta(days=1)
    months_ru = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
        5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
        9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря',
    }
    return f"{yday.day} {months_ru[yday.month]}"


def _seed_student(temp_db):
    head_id = dbm.add_parent("Head", "998900001111", role='senior')
    dbm.update_parent_telegram_id("998900001111", 111111)
    fam_id = dbm.add_family("F-race")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    sid = dbm.add_student("Kid", "ss-race")
    dbm.update_student_display_name(sid, "Kid")
    dbm.link_student_to_family(fam_id, sid)
    return sid


def test_history_sync_skips_today_when_monitor_owns_it(temp_db):
    """ГЛАВНЫЙ race-тест. До фикса проваливался: history-sync вставлял запись
    из «Все оценки» с сегодняшней датой пока monitor ещё в pending."""
    sid = _seed_student(temp_db)

    # Симулируем состояние ДО race: monitor успел вписать одиночную «2» в
    # сегодняшнюю ячейку через cell_reference="Сегодня!...".
    today = _tashkent_today().isoformat()
    dbm.add_grade(sid, "Узбекский язык", 2.0, "2", f"Сегодня!Узбекский язык:{today}")

    # Учитель в Sheets уже поменял на «2/5», но monitor пока в pending
    # (не успел сделать UPDATE). history-sync читает «Все оценки» и видит «2/5».
    sheet_data = [
        ["Все оценки", "Kid"],
        ["Оценки", _date_label_today_ru()],
        ["Узбекский язык", "2/5"],
    ]
    with patch('src.history_importer.get_sheet_data', return_value=sheet_data):
        import_history_for_student(sid, "ss-race")

    # ОЖИДАНИЕ: history-sync пропустил «сегодня». В БД только одна запись —
    # та что вставил monitor.  monitor позже сам обновит её на «2/5».
    with dbm.get_db_connection() as conn:
        rows = conn.cursor().execute(
            "SELECT raw_text, cell_reference FROM grade_history "
            "WHERE student_id=? AND subject=?",
            (sid, "Узбекский язык"),
        ).fetchall()
    assert len(rows) == 1, (
        f"Дубль! history-sync вставил вторую запись для сегодняшней даты. "
        f"Строки: {[dict(r) for r in rows]}"
    )
    # Запись из monitor осталась нетронутой
    assert rows[0]['cell_reference'].startswith("Сегодня!")


def test_history_sync_still_imports_yesterday(temp_db):
    """Контр-проверка: вчерашние даты ОБЯЗАНЫ импортироваться — monitor их
    уже не подхватит (там лист «Сегодня» содержит только current day)."""
    sid = _seed_student(temp_db)

    sheet_data = [
        ["Все оценки", "Kid"],
        ["Оценки", _date_label_yesterday_ru()],
        ["Алгебра", "4"],
    ]
    with patch('src.history_importer.get_sheet_data', return_value=sheet_data):
        result = import_history_for_student(sid, "ss-race")

    assert result['imported'] >= 1, "Вчерашняя оценка должна быть импортирована"
    with dbm.get_db_connection() as conn:
        rows = conn.cursor().execute(
            "SELECT raw_text FROM grade_history WHERE student_id=? AND subject=?",
            (sid, "Алгебра"),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]['raw_text'] == '4'


def test_parser_dedups_duplicate_columns_same_date():
    """В «Все оценки» две колонки с одинаковой датой (data quality issue
    у учителя) — наблюдалось в проде: GE6/IN6, GD6/IM6 для Английского.

    Парсер должен выдать дедуплицированный список — даже если SQL-дедуп ниже
    спасёт от записи в БД, мы не хотим плодить лишнюю работу и зависеть от
    дедупа уровня БД."""
    past = _tashkent_today() - timedelta(days=20)
    months_ru = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
        5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
        9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря',
    }
    label = f"{past.day} {months_ru[past.month]}"

    sheet_data = [
        ["Все оценки", "Kid"],
        ["Оценки", label, "пусто", label],     # та же дата в двух столбцах
        ["Английский язык", "5", "", "5"],    # одна и та же «5» дважды
    ]
    records = _parse_all_grades_sheet(sheet_data)
    eng = [r for r in records if r['subject'] == 'Английский язык']
    assert len(eng) == 1, (
        f"Парсер выдал дубликат при двух столбцах с одинаковой датой. "
        f"Записей: {[(r['raw_text'], r['col_index']) for r in eng]}"
    )
