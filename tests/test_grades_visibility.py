"""Регрессия: глава семьи должен видеть детей даже если он не залинкован
явно через family_links (только families.head_id). Реальный bug —
после `cmd_add_family_start` глава записывается в head_id, но иногда
не появляется в family_links → /grades возвращает 'нет учеников'."""
import src.database_manager as dbm


def test_head_sees_students_via_head_id_only(temp_db):
    head_id = dbm.add_parent("Head", "998900000700", role='senior')
    dbm.update_parent_telegram_id("998900000700", 700700)

    fam_id = dbm.add_family("F")
    dbm.set_family_head(fam_id, head_id)
    # ВАЖНО: НЕ делаем link_parent_to_family — эмулируем bug

    student_id = dbm.add_student("Kid", "ss-700")
    dbm.link_student_to_family(fam_id, student_id)

    students = dbm.get_students_for_parent(700700)
    # До фикса было 0 — после должно быть 1
    assert len(students) == 1
    assert students[0]['id'] == student_id


def test_member_still_sees_students_via_family_links(temp_db):
    """Контроль: обычный член семьи (через family_links) тоже видит детей."""
    member_id = dbm.add_parent("Member", "998900000701", role='senior')
    dbm.update_parent_telegram_id("998900000701", 700701)

    fam_id = dbm.add_family("F")
    dbm.link_parent_to_family(fam_id, member_id)
    student_id = dbm.add_student("Kid", "ss-701")
    dbm.link_student_to_family(fam_id, student_id)

    assert len(dbm.get_students_for_parent(700701)) == 1


def test_outsider_sees_no_students(temp_db):
    """Контроль: посторонний не видит чужих детей."""
    out_id = dbm.add_parent("Outsider", "998900000702", role='senior')
    dbm.update_parent_telegram_id("998900000702", 700702)

    head_id = dbm.add_parent("Head", "998900000703", role='senior')
    fam_id = dbm.add_family("F")
    dbm.set_family_head(fam_id, head_id)
    student_id = dbm.add_student("Kid", "ss-702")
    dbm.link_student_to_family(fam_id, student_id)

    assert dbm.get_students_for_parent(700702) == []


def test_filter_by_family_id(temp_db):
    """Если указать family_id — фильтр работает корректно."""
    head_id = dbm.add_parent("Head", "998900000704", role='senior')
    dbm.update_parent_telegram_id("998900000704", 700704)

    f1 = dbm.add_family("F1")
    f2 = dbm.add_family("F2")
    dbm.set_family_head(f1, head_id)
    dbm.set_family_head(f2, head_id)

    s1 = dbm.add_student("Kid1", "ss-1")
    s2 = dbm.add_student("Kid2", "ss-2")
    dbm.link_student_to_family(f1, s1)
    dbm.link_student_to_family(f2, s2)

    only_f1 = dbm.get_students_for_parent(700704, family_id=f1)
    assert len(only_f1) == 1
    assert only_f1[0]['id'] == s1
