"""Тесты создания/использования промокодов — закрывают регрессию SQL-injection."""
import pytest
import src.database_manager as dbm


def test_create_promo_with_int_expires(temp_db):
    assert dbm.create_promo_code("PROMO1", "monthly", expires_days=30) is True
    promo = dbm.get_promo_code("PROMO1")
    assert promo is not None
    assert promo['code'] == "PROMO1"


def test_create_promo_without_expires(temp_db):
    assert dbm.create_promo_code("FOREVER", "yearly") is True
    promo = dbm.get_promo_code("FOREVER")
    assert promo is not None
    assert promo['expires_at'] is None


def test_promo_use_count_increments(temp_db):
    dbm.create_promo_code("USE1", "monthly", max_uses=2)
    assert dbm.use_promo_code("USE1") is True
    assert dbm.use_promo_code("USE1") is True
    # Третий раз — лимит исчерпан
    assert dbm.use_promo_code("USE1") is False


def test_promo_sql_injection_attempt_is_safe(temp_db):
    """Если кто-то передал нечисловое значение в expires_days — функция возвращает
    False (через int()-конверсию) и промокод не создаётся.
    Главное — что никакой SQL не выполняется и в БД нет посторонних изменений."""
    result = dbm.create_promo_code("HACK", "monthly", expires_days="999) OR 1=1 --")
    assert result is False
    # Промокод не создан
    assert dbm.get_promo_code("HACK") is None
    # И в таблице промокодов вообще пусто
    assert len(dbm.list_promo_codes()) == 0
