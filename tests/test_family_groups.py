"""Тесты family_groups — БД-слой и авторизация."""
import pytest
import src.database_manager as dbm
from src.db.groups import (
    link_group_to_family, get_family_for_group,
    get_groups_for_family, get_groups_for_student, unlink_group,
)


def _make_family(temp_db, name="F", phone_suffix="01"):
    head_id = dbm.add_parent(f"Head_{phone_suffix}", f"99890000000{phone_suffix}", role='senior')
    fam_id = dbm.add_family(name)
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    return fam_id, head_id


def test_link_group_basic(temp_db):
    fam_id, head_id = _make_family(temp_db)
    ok = link_group_to_family(fam_id, -100123, "Family Chat", head_id)
    assert ok is True

    found = get_family_for_group(-100123)
    assert found is not None
    assert found['family_id'] == fam_id
    assert found['family_name'] == "F"


def test_chat_id_is_unique(temp_db):
    """Один chat_id нельзя привязать к двум семьям."""
    f1, h1 = _make_family(temp_db, "F1", "10")
    f2, h2 = _make_family(temp_db, "F2", "20")

    assert link_group_to_family(f1, -200, "Chat", h1) is True
    assert link_group_to_family(f2, -200, "Chat", h2) is False


def test_one_family_can_have_many_groups(temp_db):
    fam_id, head_id = _make_family(temp_db, "F", "30")
    assert link_group_to_family(fam_id, -300, "Chat A", head_id) is True
    assert link_group_to_family(fam_id, -301, "Chat B", head_id) is True

    groups = get_groups_for_family(fam_id)
    assert {g['chat_id'] for g in groups} == {-300, -301}


def test_get_groups_for_student(temp_db):
    """Если ребёнок в нескольких семьях — get_groups_for_student возвращает все группы (uniq)."""
    f1, h1 = _make_family(temp_db, "F1", "40")
    f2, h2 = _make_family(temp_db, "F2", "41")

    student_id = dbm.add_student("Kid", "ss-kid")
    dbm.link_student_to_family(f1, student_id)
    dbm.link_student_to_family(f2, student_id)

    link_group_to_family(f1, -400, "Chat F1", h1)
    link_group_to_family(f2, -401, "Chat F2", h2)

    groups = get_groups_for_student(student_id)
    assert {g['chat_id'] for g in groups} == {-400, -401}


def test_link_with_message_thread_id(temp_db):
    """Супергруппа с темами — message_thread_id сохраняется и возвращается."""
    fam_id, head_id = _make_family(temp_db, "Forum", "70")
    ok = link_group_to_family(fam_id, -700, "Forum", head_id, message_thread_id=42)
    assert ok is True

    found = get_family_for_group(-700)
    assert found['message_thread_id'] == 42

    student_id = dbm.add_student("K", "ss-k")
    dbm.link_student_to_family(fam_id, student_id)
    groups = get_groups_for_student(student_id)
    assert len(groups) == 1
    assert groups[0]['chat_id'] == -700
    assert groups[0]['message_thread_id'] == 42


def test_link_without_thread_id_defaults_to_none(temp_db):
    """Обычная группа без тем — message_thread_id = NULL."""
    fam_id, head_id = _make_family(temp_db, "Plain", "80")
    link_group_to_family(fam_id, -800, "Plain", head_id)
    found = get_family_for_group(-800)
    assert found['message_thread_id'] is None


def test_update_group_thread_sets_and_clears(temp_db):
    from src.db.groups import update_group_thread
    fam_id, head_id = _make_family(temp_db, "Forum", "90")
    link_group_to_family(fam_id, -900, "Forum", head_id)

    # Установить
    assert update_group_thread(-900, 42) is True
    assert get_family_for_group(-900)['message_thread_id'] == 42

    # Сбросить
    assert update_group_thread(-900, None) is True
    assert get_family_for_group(-900)['message_thread_id'] is None

    # Несуществующий chat
    assert update_group_thread(-9999, 1) is False


@pytest.mark.parametrize("link,expected", [
    ("https://t.me/c/1234567890/123", 123),
    ("https://t.me/c/1234567890/123/456", 123),  # link на сообщение в теме
    ("https://t.me/mygroup/789", 789),
    ("https://t.me/mygroup/789/1011", 789),
    ("http://t.me/c/1/55", 55),
    ("  https://t.me/c/1/55  ", 55),  # пробелы по краям
    ("not a link", None),
    ("https://example.com/c/1/2", None),
    ("", None),
    ("https://t.me/c/abc/def", None),  # нечисловой topic
])
def test_parse_topic_link(link, expected):
    from src.group_utils import parse_topic_link
    assert parse_topic_link(link) == expected


def test_unlink_group(temp_db):
    fam_id, head_id = _make_family(temp_db, "F", "50")
    link_group_to_family(fam_id, -500, "Chat", head_id)

    assert unlink_group(-500) is True
    assert unlink_group(-500) is False  # уже отвязан
    assert get_family_for_group(-500) is None


def test_cascade_delete_family_removes_groups(temp_db):
    """При удалении семьи — связанные группы тоже должны исчезнуть."""
    from src.db.maintenance import delete_family_cascade
    fam_id, head_id = _make_family(temp_db, "F", "60")
    link_group_to_family(fam_id, -600, "Chat", head_id)

    delete_family_cascade(fam_id)

    assert get_family_for_group(-600) is None
