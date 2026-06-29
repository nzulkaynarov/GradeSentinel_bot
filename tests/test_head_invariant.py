"""Тесты инварианта: глава семьи всегда залинкован в family_links.

После фикса 2026-05-01 set_family_head атомарно создаёт запись в family_links
если её ещё нет. Плюс init_db делает backfill для исторических данных.
Эти тесты — гарантия что регрессия не вернётся.
"""
import pytest
import src.database_manager as dbm


def test_set_family_head_creates_family_link(temp_db):
    """Прямой вызов set_family_head без предварительного link_parent_to_family
    должен сам создать запись в family_links."""
    parent_id = dbm.add_parent("Head", "998900000900", role='senior')
    fam_id = dbm.add_family("F")

    # Эмулируем «голый» вызов — никаких link до этого
    dbm.set_family_head(fam_id, parent_id)

    # Проверяем что глава появился в family_links
    with dbm.get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            'SELECT 1 FROM family_links WHERE family_id = %s AND parent_id = %s',
            (fam_id, parent_id),
        )
        assert c.fetchone() is not None


def test_set_family_head_idempotent(temp_db):
    """Повторный вызов set_family_head не дублирует family_links."""
    parent_id = dbm.add_parent("Head", "998900000901", role='senior')
    fam_id = dbm.add_family("F")
    dbm.link_parent_to_family(fam_id, parent_id)
    dbm.set_family_head(fam_id, parent_id)
    dbm.set_family_head(fam_id, parent_id)

    with dbm.get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            'SELECT COUNT(*) as n FROM family_links WHERE family_id = %s AND parent_id = %s AND student_id IS NULL',
            (fam_id, parent_id),
        )
        # Только одна запись parent↔family без student
        assert c.fetchone()['n'] == 1


def test_backfill_migration_links_orphan_heads(temp_db):
    """init_db должен залинковать всех глав, которые числятся head_id но не в family_links.

    Эмулируем historical state — создаём БД с set_family_head БЕЗ insert в family_links
    (через прямой SQL обход), затем переинициализируем init_db и проверяем backfill."""
    # Первый запуск — создаём схему
    dbm.init_db()

    # Создаём «исторический» bug: head_id есть, family_links нет.
    # Прямым SQL минуем set_family_head чтобы воспроизвести старое состояние.
    with dbm.get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO parents (fio, phone, role) VALUES ('Head', '998900000902', 'senior') RETURNING id"
        )
        head_id = c.fetchone()[0]
        c.execute(
            "INSERT INTO families (family_name, head_id) VALUES ('F', %s) RETURNING id",
            (head_id,),
        )
        fam_id = c.fetchone()[0]
        # НЕ инсёртим в family_links — это тот самый bug

    # Подтверждаем что bug воспроизведён
    with dbm.get_db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) as n FROM family_links WHERE family_id = %s', (fam_id,))
        assert c.fetchone()['n'] == 0

    # Повторный init_db — должен сделать backfill
    dbm.init_db()

    with dbm.get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            'SELECT COUNT(*) as n FROM family_links WHERE family_id = %s AND parent_id = %s',
            (fam_id, head_id),
        )
        assert c.fetchone()['n'] == 1


def test_backfill_idempotent_on_clean_db(temp_db):
    """Backfill не должен ничего делать на нормальной БД (без orphan-глав)."""
    dbm.init_db()
    head_id = dbm.add_parent("Head", "998900000903", role='senior')
    fam_id = dbm.add_family("F")
    dbm.set_family_head(fam_id, head_id)  # уже линкует

    # Повторный init не должен создать дубликат
    dbm.init_db()

    with dbm.get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            'SELECT COUNT(*) as n FROM family_links WHERE family_id = %s AND parent_id = %s',
            (fam_id, head_id),
        )
        assert c.fetchone()['n'] == 1
