"""Тесты авторизации в БД-слое — после критических фиксов от 2026-04.

Покрывают:
- can_manage_family (admin / head / посторонний)
- is_member_of_family
- atomic use_invite (race protection)
"""
import pytest
import src.database_manager as dbm


def _setup_family_with_users(temp_db):
    """Создаёт типичную конфигурацию: админ, глава, член, посторонний."""
    admin_id = dbm.add_parent("Admin", "998900000001", role="admin")
    dbm.update_parent_telegram_id("998900000001", 100001)

    head_id = dbm.add_parent("Head", "998900000002", role="senior")
    dbm.update_parent_telegram_id("998900000002", 100002)

    member_id = dbm.add_parent("Member", "998900000003", role="senior")
    dbm.update_parent_telegram_id("998900000003", 100003)

    outsider_id = dbm.add_parent("Outsider", "998900000004", role="senior")
    dbm.update_parent_telegram_id("998900000004", 100004)

    family_id = dbm.add_family("TestFamily")
    dbm.set_family_head(family_id, head_id)
    dbm.link_parent_to_family(family_id, head_id)
    dbm.link_parent_to_family(family_id, member_id)

    return {
        'admin_tg': 100001,
        'head_tg': 100002,
        'member_tg': 100003,
        'outsider_tg': 100004,
        'family_id': family_id,
        'head_id': head_id,
    }


def test_admin_can_manage_any_family(temp_db):
    ctx = _setup_family_with_users(temp_db)
    assert dbm.can_manage_family(ctx['admin_tg'], ctx['family_id']) is True


def test_head_can_manage_own_family(temp_db):
    ctx = _setup_family_with_users(temp_db)
    assert dbm.can_manage_family(ctx['head_tg'], ctx['family_id']) is True


def test_member_cannot_manage_family(temp_db):
    """Обычный член (не глава, не админ) не может управлять семьёй."""
    ctx = _setup_family_with_users(temp_db)
    assert dbm.can_manage_family(ctx['member_tg'], ctx['family_id']) is False


def test_outsider_cannot_manage_family(temp_db):
    ctx = _setup_family_with_users(temp_db)
    assert dbm.can_manage_family(ctx['outsider_tg'], ctx['family_id']) is False


def test_outsider_cannot_manage_unknown_family(temp_db):
    """Несуществующая семья → доступа нет ни у кого, кроме админа."""
    ctx = _setup_family_with_users(temp_db)
    assert dbm.can_manage_family(ctx['outsider_tg'], 9999) is False


def test_member_is_member(temp_db):
    ctx = _setup_family_with_users(temp_db)
    assert dbm.is_member_of_family(ctx['member_tg'], ctx['family_id']) is True


def test_outsider_is_not_member(temp_db):
    ctx = _setup_family_with_users(temp_db)
    assert dbm.is_member_of_family(ctx['outsider_tg'], ctx['family_id']) is False


def test_use_invite_is_atomic_single_use(temp_db):
    """Дважды использовать один инвайт нельзя (защита от гонки)."""
    ctx = _setup_family_with_users(temp_db)
    code = dbm.create_invite(ctx['family_id'], ctx['head_id'], expires_hours=48)

    first = dbm.use_invite(code, ctx['head_id'])
    second = dbm.use_invite(code, ctx['head_id'])

    assert first is True
    assert second is False
