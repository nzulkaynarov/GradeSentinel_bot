"""Регрессия: morning flush больше не отправляет 14 копий одной оценки.

Defense in depth после инцидента 2026-05-21 — даже если monitor (или
любой будущий writer) положит дубликат в notification_queue, morning
flush его отфильтрует. Этот тест проверяет хелпер `_dedup_preserve_order`.
"""
from src.schedulers import _dedup_preserve_order


def test_dedup_empty():
    assert _dedup_preserve_order([]) == []


def test_dedup_no_duplicates():
    msgs = ["a", "b", "c"]
    assert _dedup_preserve_order(msgs) == ["a", "b", "c"]


def test_dedup_removes_exact_duplicates():
    msgs = ["a", "b", "a", "c", "b", "a"]
    assert _dedup_preserve_order(msgs) == ["a", "b", "c"]


def test_dedup_preserves_order_of_first_occurrence():
    """Если 'b' встречается раньше 'a' в финальном списке — первая позиция."""
    msgs = ["b", "a", "b", "a", "b"]
    assert _dedup_preserve_order(msgs) == ["b", "a"]


def test_dedup_regression_2026_05_21_spam():
    """Воспроизведение конкретного инцидента: 14 копий двух разных уведомлений.

    До фикса: monitor шёл по cell_reference race condition каждые 5 минут,
    `queue_notification` писало 14 копий двух оценок. Morning flush слал их
    все как 14 отдельных сообщений через ➖➖➖. С dedup — только 2.
    """
    algebra = "🌟 Алгебра 4 ..."
    literature = "🌟 Литература 5 ..."
    queue = [algebra, literature] * 7  # 14 сообщений (7 циклов × 2 оценки)
    deduped = _dedup_preserve_order(queue)
    assert deduped == [algebra, literature]
    assert len(deduped) == 2


def test_dedup_handles_html_messages():
    """Сообщения с HTML — обычные строки, сравнение exact match."""
    msg_a = "<b>Алгебра</b>: 4"
    msg_b = "<b>Литература</b>: 5"
    assert _dedup_preserve_order([msg_a, msg_b, msg_a]) == [msg_a, msg_b]
