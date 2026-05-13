import re
from typing import List, Optional, Tuple

# Один сегмент оценки: "5", "5-", "4+", "4=", "4.5", "5.0".
_GRADE_PIECE_RE = re.compile(r'^([1-5])([.\-+=]?\d?)$')

# Спец-токены (отсутствие/болезнь/освобождение/н-а/б-ф).
# СО слэшами — их нельзя разрывать по "/" как multi-grade.
_SPECIAL_WORDS = {'н', 'б', 'н/а', 'осв', 'болел', 'болела', 'ув', 'дз', 'см', 'б/ф'}


def _parse_piece(piece: str) -> Optional[Tuple[Optional[float], str]]:
    """Парсит один сегмент. None — не оценка и не спец-слово (мусор)."""
    p = piece.strip()
    if not p:
        return None
    m = _GRADE_PIECE_RE.match(p)
    if m:
        return float(m.group(1)), p
    if p.lower() in _SPECIAL_WORDS:
        return None, p
    return None


def sanitize_cell(raw_string) -> List[Tuple[Optional[float], str]]:
    """Парсит содержимое ячейки в список оценок.

    Возвращает list[(grade_value, raw_text)]:
        - "5"     -> [(5.0, "5")]
        - "2/5"   -> [(2.0, "2"), (5.0, "5")]
        - "н"     -> [(None, "н")]
        - "н/а"   -> [(None, "н/а")]              # спец-слово со слэшом
        - "2/2/3" -> [(2.0, "2"), (2.0, "2"), (3.0, "3")]
        - ""      -> []
        - "XYZ"   -> []                            # мусор
        - "5/X"   -> []                            # один из сегментов мусор → весь cell мусор
    """
    if not isinstance(raw_string, str):
        raw_string = str(raw_string)
    clean = raw_string.strip()
    if not clean:
        return []

    # Сначала спец-слова целиком — чтобы "н/а", "б/ф" не разрывались по "/".
    if clean.lower() in _SPECIAL_WORDS:
        return [(None, clean)]

    if '/' in clean:
        parts = clean.split('/')
        results: List[Tuple[Optional[float], str]] = []
        for part in parts:
            parsed = _parse_piece(part)
            if parsed is None:
                # Любой невалидный сегмент → весь cell мусор. Безопаснее
                # чем ложно парсить "5/мусор" как просто "5".
                return []
            results.append(parsed)
        return results

    parsed = _parse_piece(clean)
    return [parsed] if parsed else []


def sanitize_grade(raw_string) -> Tuple[Optional[float], Optional[str]]:
    """Backward-compatible одиночная оценка.

    Возвращает:
        - (5.0, "5")     для одиночной оценки
        - (None, "н")    для спец-слова
        - (None, "")     для пустой строки
        - (None, None)   для мусора или multi-grade ("2/5") — caller должен
          использовать sanitize_cell если ожидает X/Y формат

    Используется в четвертных оценках (quarters), где формат заведомо одиночный.
    """
    if not isinstance(raw_string, str):
        raw_string = str(raw_string)
    if not raw_string.strip():
        return None, ""

    cells = sanitize_cell(raw_string)
    if len(cells) == 1:
        return cells[0]
    # Пусто (мусор) или multi-grade — caller должен использовать sanitize_cell.
    return None, None


if __name__ == "__main__":
    # Smoke-тесты для backward-compat
    test_cases = [
        ("5-", (5.0, "5-")),
        ("4+", (4.0, "4+")),
        ("3", (3.0, "3")),
        ("н", (None, "н")),
        ("н/а", (None, "н/а")),
        ("болел", (None, "болел")),
        ("", (None, "")),
        ("отлично", (None, None)),
        ("2/5", (None, None)),  # caller должен использовать sanitize_cell
    ]
    for raw, expected in test_cases:
        got = sanitize_grade(raw)
        assert got == expected, f"sanitize_grade({raw!r}) = {got}, expected {expected}"
    print("sanitize_grade smoke OK")
