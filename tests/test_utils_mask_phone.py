"""Тест mask_phone — PII должен маскироваться в логах."""
import pytest
from src.utils import mask_phone


@pytest.mark.parametrize("raw,expected", [
    ("998901234567", "***4567"),
    ("+998 90 123-45-67", "***4567"),
    ("123", "***"),       # слишком короткий — полностью маскируется
    ("", "***"),
    (None, "***"),
])
def test_mask_phone(raw, expected):
    assert mask_phone(raw) == expected


def test_mask_phone_no_full_number_in_output():
    """Гарантируем что в выводе нет полного номера."""
    full = "998901234567"
    masked = mask_phone(full)
    # Только последние 4 цифры
    assert "9012345" not in masked
    assert "67" not in masked or masked.endswith("4567")
