"""Регрессия: двухфазное подтверждение оценок + multi-grade в monitor_engine.

Проверяем именно то от чего страдают клиенты в проде:
- Учитель ввёл «5», бот моментально шлёт уведомление, через минуту учитель
  стирает → у родителя остаётся «оценка-призрак». ⇒ Бот ДОЛЖЕН дождаться
  второго цикла polling перед уведомлением.
- Учитель меняет «2» на «2/5» (добавил вторую оценку за день). До фикса
  sanitize_grade возвращал (None, None) и бот молчал, родитель видел
  расхождение. ⇒ Должен прислать «обновление: 2 → 2/5».
"""
from unittest.mock import patch, MagicMock

import pytest

import src.database_manager as dbm
import src.monitor_engine as me


@pytest.fixture
def setup_student(temp_db):
    """Создаёт активную семью с подпиской и одним учеником."""
    head_id = dbm.add_parent("Head", "998900000999", role='senior')
    dbm.update_parent_telegram_id("998900000999", 999999)
    fam_id = dbm.add_family("F-pending")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    student_id = dbm.add_student("Kid", "ss-pending")
    dbm.update_student_display_name(student_id, "Kid Display")
    dbm.link_student_to_family(fam_id, student_id)
    # Активная подписка чтобы попасть в get_active_spreadsheets_with_subscription
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)).strftime('%Y-%m-%d')
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "UPDATE families SET subscription_end = ? WHERE id = ?",
            (future, fam_id),
        )
    return {'student_id': student_id, 'tg_id': 999999, 'spreadsheet_id': 'ss-pending'}


def _make_sheet(grade_for_subject: dict) -> list:
    """Эмулирует ответ Sheets API для листа «Сегодня» (A1:B50)."""
    rows = [["Сегодня", "Kid Display"], ["Оценки", "13 мая ср"]]
    for subj, value in grade_for_subject.items():
        rows.append([subj, value])
    return rows


@pytest.fixture(autouse=True)
def reset_pending():
    """Очищаем in-memory pending между тестами."""
    me._pending_grades.clear()
    yield
    me._pending_grades.clear()


def _run_cycle(sheet_data):
    """Один цикл polling с замоканным Sheets API и захватом уведомлений."""
    sent = []

    def fake_send(tg_ids, message, inline_markup=None, force=False):
        for tg in tg_ids:
            msg = message[tg] if isinstance(message, dict) else message
            sent.append({'tg_id': tg, 'msg': msg})

    with patch('src.monitor_engine.get_sheet_data', return_value=sheet_data), \
         patch('src.monitor_engine.get_spreadsheet_title', return_value="Kid Display"), \
         patch('src.monitor_engine.send_notification', side_effect=fake_send), \
         patch('src.monitor_engine._send_to_groups_for_student'):
        me._check_for_new_grades_impl()
    return sent


# ─── Сценарий 1: одиночное явление «5» (типо учителя) ────────────────
def test_flicker_single_appearance_does_not_notify(setup_student):
    """«5» появилась на 1 цикл и исчезла → НЕ должна быть уведомлена/записана."""
    info = setup_student

    # Цикл 1: «5» в Алгебре
    sent_1 = _run_cycle(_make_sheet({"Алгебра": "5"}))
    assert sent_1 == [], "На первом обнаружении нельзя сразу слать — даём цикл подтверждения"

    # БД ещё не должна содержать запись (ждём подтверждения)
    grades = dbm.get_grade_history_for_student(info['student_id'], days=30)
    assert len(grades) == 0


# ─── Сценарий 2: стабильное «5» через 2 цикла ────────────────────────
def test_stable_grade_confirmed_after_two_cycles(setup_student):
    info = setup_student

    sent_1 = _run_cycle(_make_sheet({"Алгебра": "5"}))
    assert sent_1 == []

    sent_2 = _run_cycle(_make_sheet({"Алгебра": "5"}))
    assert len(sent_2) == 1, "После подтверждения должно прийти уведомление"
    assert "Алгебра" in sent_2[0]['msg']
    assert "5" in sent_2[0]['msg']

    # В БД появилась запись
    grades = dbm.get_grade_history_for_student(info['student_id'], days=30)
    assert any(g['subject'] == 'Алгебра' and g['raw_text'] == '5' for g in grades)


# ─── Сценарий 3: «5» сменилось на «4» — уведомляем только о финальной ─
def test_typo_then_correction_notifies_only_final(setup_student):
    info = setup_student

    # Цикл 1: учитель опечатался «5»
    assert _run_cycle(_make_sheet({"Алгебра": "5"})) == []
    # Цикл 2: исправил на «4»
    assert _run_cycle(_make_sheet({"Алгебра": "4"})) == []
    # Цикл 3: «4» стабильно
    sent_3 = _run_cycle(_make_sheet({"Алгебра": "4"}))

    assert len(sent_3) == 1
    msg = sent_3[0]['msg']
    assert "4" in msg
    # Про «5» уведомлять НЕЛЬЗЯ
    grades = dbm.get_grade_history_for_student(info['student_id'], days=30)
    saved = [g for g in grades if g['subject'] == 'Алгебра']
    assert len(saved) == 1
    assert saved[0]['raw_text'] == '4'


# ─── Сценарий 4: исчезновение между циклами не сохраняет в БД ─────────
def test_grade_disappears_no_notification(setup_student):
    info = setup_student

    _run_cycle(_make_sheet({"Алгебра": "5"}))  # pending
    sent_2 = _run_cycle(_make_sheet({}))        # учитель стёр

    assert sent_2 == []
    grades = dbm.get_grade_history_for_student(info['student_id'], days=30)
    assert len([g for g in grades if g['subject'] == 'Алгебра']) == 0


# ─── Сценарий 5: «2» → «2/5» (добавление второй оценки в день) ────────
def test_augmentation_two_to_two_slash_five(setup_student):
    """Главный кейс из реальной жалобы: «2» в БД, в таблице теперь «2/5».
    Бот должен прислать уточнение «2 → 2/5», а в БД raw_text='2/5'."""
    info = setup_student

    # Сначала стабилизируем «2» (2 цикла)
    _run_cycle(_make_sheet({"Узбекский язык": "2"}))
    sent_initial = _run_cycle(_make_sheet({"Узбекский язык": "2"}))
    assert len(sent_initial) == 1
    assert "Узбекский язык" in sent_initial[0]['msg']

    # Теперь учитель добавил «5» → ячейка «2/5»
    sent_pending = _run_cycle(_make_sheet({"Узбекский язык": "2/5"}))
    assert sent_pending == [], "Первое появление 2/5 — pending, без уведомления"

    sent_confirmed = _run_cycle(_make_sheet({"Узбекский язык": "2/5"}))
    assert len(sent_confirmed) == 1, "После подтверждения шлём «обновление»"
    msg = sent_confirmed[0]['msg']
    assert "2" in msg and "2/5" in msg, f"Должно быть видно переход 2 → 2/5. msg={msg!r}"

    # В БД raw_text обновился до «2/5»
    grades = dbm.get_grade_history_for_student(info['student_id'], days=30)
    saved = [g for g in grades if g['subject'] == 'Узбекский язык']
    assert len(saved) == 1
    assert saved[0]['raw_text'] == '2/5'


# ─── Сценарий 6: «2/5» → «2» (учитель убрал вторую оценку) ────────────
def test_trim_does_not_notify(setup_student):
    """Если учитель УБРАЛ оценку из ячейки — родителю не пишем (не сюрприз).
    Но БД должна синхронизироваться, чтобы дашборд показывал правду."""
    info = setup_student

    _run_cycle(_make_sheet({"Узбекский язык": "2/5"}))
    sent_confirmed = _run_cycle(_make_sheet({"Узбекский язык": "2/5"}))
    assert len(sent_confirmed) == 1  # подтвердили исходное «2/5»

    # Теперь учитель снял «5», осталось «2»
    sent_trim = _run_cycle(_make_sheet({"Узбекский язык": "2"}))
    assert sent_trim == [], "При сокращении оценок уведомления не шлём"

    # БД синхронизировалась мгновенно (без второго цикла — это «убирание»,
    # не добавление, риск ложного уведомления нулевой)
    grades = dbm.get_grade_history_for_student(info['student_id'], days=30)
    saved = [g for g in grades if g['subject'] == 'Узбекский язык']
    assert len(saved) == 1
    assert saved[0]['raw_text'] == '2'


# ─── Сценарий 7: пустая ячейка с нуля «» → «2/5» (новая запись) ───────
def test_new_cell_multi_grade(setup_student):
    info = setup_student

    _run_cycle(_make_sheet({"Алгебра": "2/5"}))
    sent = _run_cycle(_make_sheet({"Алгебра": "2/5"}))

    assert len(sent) == 1
    msg = sent[0]['msg']
    assert "Алгебра" in msg
    assert "2/5" in msg

    grades = dbm.get_grade_history_for_student(info['student_id'], days=30)
    saved = [g for g in grades if g['subject'] == 'Алгебра']
    assert len(saved) == 1
    assert saved[0]['raw_text'] == '2/5'


# ─── Сценарий 8: мусор/дата в B (заголовок) не уведомляет ─────────────
def test_garbage_in_header_row_ignored(setup_student):
    """Строка 2 «Оценки | 13 мая ср» не должна порождать уведомлений."""
    info = setup_student

    _run_cycle(_make_sheet({}))
    sent = _run_cycle(_make_sheet({}))
    assert sent == []

    grades = dbm.get_grade_history_for_student(info['student_id'], days=30)
    # Никаких «Оценки» / «13 мая ср» как оценок
    assert all(g['subject'] != 'Оценки' for g in grades)


# ─── Unit: _compute_added_grades multiset diff ────────────────────────
def test_compute_added_pure_diff():
    old = [(2.0, "2")]
    new = [(2.0, "2"), (5.0, "5")]
    assert me._compute_added_grades(old, new) == [(5.0, "5")]


def test_compute_added_replacement():
    old = [(2.0, "2")]
    new = [(3.0, "3")]
    assert me._compute_added_grades(old, new) == [(3.0, "3")]


def test_compute_added_trim():
    old = [(2.0, "2"), (5.0, "5")]
    new = [(2.0, "2")]
    assert me._compute_added_grades(old, new) == []


def test_compute_added_dupe():
    old = [(2.0, "2")]
    new = [(2.0, "2"), (2.0, "2")]
    assert me._compute_added_grades(old, new) == [(2.0, "2")]


# ─── Cross-domain dedup: monitor vs history_importer cell_reference ────
# Регрессия инцидента 2026-05-21: history_importer пишет cell_reference в
# формате "Все оценки!JC7", monitor ищет по "Сегодня!{subject}:{date}".
# Без content-key проверки monitor шлёт уведомление каждый polling-цикл.
def test_grade_exists_by_content_basic(temp_db):
    """grade_exists_by_content находит по UNIQUE-ключу, игнорируя cell_reference."""
    student_id = dbm.add_student("Kid", "ss")
    # Кладём как history_importer: cell_reference другого формата
    dbm.add_grade(student_id, "Алгебра", 4.0, "4", "Все оценки!JC7",
                  grade_date="2026-05-21")

    # Поиск по content-key должен найти
    assert dbm.grade_exists_by_content(student_id, "Алгебра", "2026-05-21", "4")
    # А по monitor-формату cell_reference — нет (это и есть баг до фикса)
    assert dbm.get_existing_grade(student_id, "Сегодня!Алгебра:2026-05-21") is None
    # Другое значение — не находится
    assert not dbm.grade_exists_by_content(student_id, "Алгебра", "2026-05-21", "5")
    # Другая дата — не находится
    assert not dbm.grade_exists_by_content(student_id, "Алгебра", "2026-05-20", "4")


def test_get_existing_grade_by_content_returns_current_value(temp_db):
    """Основной identity-lookup для monitor: возвращает текущую запись
    независимо от cell_reference."""
    student_id = dbm.add_student("Kid", "ss")
    # Кладём с «чужим» cell_reference (как history_importer)
    dbm.add_grade(student_id, "Алгебра", 4.0, "4", "Все оценки!JC7",
                  grade_date="2026-05-21")

    found = dbm.get_existing_grade_by_content(student_id, "Алгебра", "2026-05-21")
    assert found is not None
    assert found['raw_text'] == "4"
    assert found['subject'] == "Алгебра"
    assert found['cell_reference'] == "Все оценки!JC7"  # origin metadata сохранилось

    # Другая дата — None
    assert dbm.get_existing_grade_by_content(student_id, "Алгебра", "2026-05-20") is None
    # Другой предмет — None
    assert dbm.get_existing_grade_by_content(student_id, "Литература", "2026-05-21") is None


def test_update_grade_by_content_does_not_touch_cell_reference(temp_db):
    """update_grade_by_content обновляет grade_value/raw_text, cell_reference
    оставляет (он теперь metadata, не identity)."""
    student_id = dbm.add_student("Kid", "ss")
    dbm.add_grade(student_id, "Алгебра", 4.0, "4", "Все оценки!JC7",
                  grade_date="2026-05-21")

    ok = dbm.update_grade_by_content(student_id, "Алгебра", "2026-05-21", 4.5, "4/5")
    assert ok

    after = dbm.get_existing_grade_by_content(student_id, "Алгебра", "2026-05-21")
    assert after['raw_text'] == "4/5"
    assert after['grade_value'] == 4.5
    assert after['cell_reference'] == "Все оценки!JC7"  # metadata cohabитирует


def test_update_grade_by_content_no_match(temp_db):
    """False если такой записи нет."""
    student_id = dbm.add_student("Kid", "ss")
    assert not dbm.update_grade_by_content(student_id, "Алгебра", "2026-05-21", 4.0, "4")


def test_monitor_skips_grade_already_written_by_history_importer(setup_student, monkeypatch):
    """Главный регрессионный тест: оценка в БД от history_importer с «чужим»
    cell_reference. Monitor должен НЕ слать уведомление и НЕ заходить в pending.
    Без фикса — спам каждые 5 минут (инцидент 2026-05-21)."""
    info = setup_student
    from datetime import datetime, timedelta, timezone
    tashkent_today = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date().isoformat()

    # Эмулируем history_importer: оценка уже в БД с cell_reference из «Все оценки!»
    dbm.add_grade(info['student_id'], "Алгебра", 4.0, "4", "Все оценки!JC7",
                  grade_date=tashkent_today)

    # Monitor читает «Сегодня!» и видит ту же оценку
    sent_1 = _run_cycle(_make_sheet({"Алгебра": "4"}))
    assert sent_1 == [], "Цикл 1: monitor должен распознать оценку как уже известную"

    # Второй цикл — тоже тишина (без фикса тут было бы [NEW GRADE])
    sent_2 = _run_cycle(_make_sheet({"Алгебра": "4"}))
    assert sent_2 == [], "Цикл 2: всё ещё тишина, никакого pending→confirm спама"

    # В БД должна остаться ОДНА запись (UNIQUE по content-key защищает)
    grades = dbm.get_grade_history_for_student(info['student_id'], days=30)
    algebra = [g for g in grades if g['subject'] == 'Алгебра']
    assert len(algebra) == 1
    assert algebra[0]['raw_text'] == '4'
