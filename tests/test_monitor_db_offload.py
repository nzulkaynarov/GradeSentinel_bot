"""B12: DB-запись display_name и failure-счётчик вынесены из fetch-воркеров.

Раньше `_fetch_student_sheet` крутился внутри ThreadPoolExecutor(FETCH_WORKERS=8)
и писал в БД (`update_student_display_name`) + трогал `_student_failure_counts` —
то есть 8 параллельных потоков брали соединения из пула (DB_POOL_MAX=5) вместе с
main-хендлерами / scheduler'ом / heartbeat'ом → риск PoolTimeout.

После рефакторинга воркер выполняет ТОЛЬКО сетевые операции (Google Sheets), а все
DB-операции идут в последовательной фазе (после as_completed) в единственном
monitor-потоке. Тесты гарантируют, что поведение при переносе сохранилось:

  1. воркер `_fetch_student_sheet` НЕ обращается к БД;
  2. display_name всё равно записывается в БД по итогам цикла;
  3. failure-счётчик растёт при недоступности таблицы и сбрасывается при успехе.
"""
from unittest.mock import patch

import pytest

import src.database_manager as dbm
import src.monitor_engine as me


@pytest.fixture(autouse=True)
def _reset_counters():
    """Изолируем in-memory состояние monitor'а между тестами."""
    me._pending_grades.clear()
    me._student_failure_counts.clear()
    me._last_failure_alert.clear()
    yield
    me._pending_grades.clear()
    me._student_failure_counts.clear()
    me._last_failure_alert.clear()


@pytest.fixture
def student_without_display_name(temp_db):
    """Активная семья с подпиской и учеником БЕЗ display_name (NULL)."""
    head_id = dbm.add_parent("Head", "998900000111", role='senior')
    dbm.update_parent_telegram_id("998900000111", 111111)
    fam_id = dbm.add_family("F-offload")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    # display_name НЕ задаём — воркер должен вычислить его из заголовка таблицы
    student_id = dbm.add_student("Kid FIO", "ss-offload")
    dbm.link_student_to_family(fam_id, student_id)
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)).strftime('%Y-%m-%d')
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "UPDATE families SET subscription_end = %s WHERE id = %s",
            (future, fam_id),
        )
    return {'student_id': student_id, 'tg_id': 111111, 'spreadsheet_id': 'ss-offload'}


# ─── 1. Воркер не трогает БД ──────────────────────────────────────────
def test_fetch_worker_does_not_touch_db():
    """`_fetch_student_sheet` — только сеть. Никаких update_student_display_name
    из воркера (это и есть суть B12: воркеры не выбирают соединения из пула)."""
    student = {'student_id': 7, 'fio': 'Kid FIO', 'spreadsheet_id': 'ss', 'display_name': None}

    with patch('src.monitor_engine.get_spreadsheet_title', return_value="Sheet Title") as m_title, \
         patch('src.monitor_engine.get_sheet_data', return_value=[["a"]]) as m_data, \
         patch('src.monitor_engine.update_student_display_name') as m_update, \
         patch('src.monitor_engine.get_db_connection') as m_conn:
        result = me._fetch_student_sheet(student, "Range!A1:B2")

    # Воркер сходил в сеть, но НЕ в БД
    m_title.assert_called_once()
    m_data.assert_called_once()
    m_update.assert_not_called()
    m_conn.assert_not_called()

    # Вернул структуру с флагом «надо записать display_name позже»
    assert result.data == [["a"]]
    assert result.display_name == "Sheet Title"  # clean_student_name("Sheet Title")
    assert result.persist_display_name is True


def test_fetch_worker_no_persist_when_display_name_cached():
    """Если display_name уже есть — воркер не дёргает даже get_spreadsheet_title."""
    student = {'student_id': 8, 'fio': 'Kid', 'spreadsheet_id': 'ss', 'display_name': 'Cached'}
    with patch('src.monitor_engine.get_spreadsheet_title') as m_title, \
         patch('src.monitor_engine.get_sheet_data', return_value=[["x"]]):
        result = me._fetch_student_sheet(student, "R")
    m_title.assert_not_called()
    assert result.display_name == "Cached"
    assert result.persist_display_name is False


# ─── 2. display_name всё равно записывается по итогам цикла ────────────
def test_display_name_persisted_after_cycle(student_without_display_name):
    """Полный цикл: у ученика не было display_name → после цикла он в БД."""
    info = student_without_display_name

    with patch('src.monitor_engine.get_sheet_data', return_value=[["Оценки все даты"], ["Оценки"]]), \
         patch('src.monitor_engine.get_spreadsheet_title', return_value="Ученик Красивое Имя"), \
         patch('src.monitor_engine.send_notification'), \
         patch('src.monitor_engine._send_to_groups_for_student'):
        me._check_for_new_grades_impl()

    with dbm.get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT display_name FROM students WHERE id = %s", (info['student_id'],))
        row = cur.fetchone()
    assert row['display_name'] == "Ученик Красивое Имя"


# ─── 3. Failure-счётчик работает из последовательной фазы ──────────────
def test_failure_counter_increments_on_none_data(student_without_display_name):
    """get_sheet_data вернул None → _record_student_failure сработал в
    последовательной фазе (счётчик вырос)."""
    info = student_without_display_name
    sid = info['student_id']

    with patch('src.monitor_engine.get_sheet_data', return_value=None), \
         patch('src.monitor_engine.get_spreadsheet_title', return_value="Kid"), \
         patch('src.monitor_engine.send_notification'), \
         patch('src.monitor_engine._send_to_groups_for_student'):
        me._check_for_new_grades_impl()

    assert me._student_failure_counts[sid] == 1

    # Второй сломанный цикл → 2 подряд
    with patch('src.monitor_engine.get_sheet_data', return_value=None), \
         patch('src.monitor_engine.get_spreadsheet_title', return_value="Kid"), \
         patch('src.monitor_engine.send_notification'), \
         patch('src.monitor_engine._send_to_groups_for_student'):
        me._check_for_new_grades_impl()

    assert me._student_failure_counts[sid] == 2


def test_failure_counter_resets_on_success(student_without_display_name):
    """После сломанного цикла успешный сбрасывает счётчик неудач."""
    info = student_without_display_name
    sid = info['student_id']

    # Ломаем
    with patch('src.monitor_engine.get_sheet_data', return_value=None), \
         patch('src.monitor_engine.get_spreadsheet_title', return_value="Kid"), \
         patch('src.monitor_engine.send_notification'), \
         patch('src.monitor_engine._send_to_groups_for_student'):
        me._check_for_new_grades_impl()
    assert me._student_failure_counts[sid] == 1

    # Успех (пустой, но валидный лист) → счётчик сброшен
    with patch('src.monitor_engine.get_sheet_data', return_value=[["Оценки все даты"], ["Оценки"]]), \
         patch('src.monitor_engine.get_spreadsheet_title', return_value="Kid"), \
         patch('src.monitor_engine.send_notification'), \
         patch('src.monitor_engine._send_to_groups_for_student'):
        me._check_for_new_grades_impl()

    assert sid not in me._student_failure_counts
