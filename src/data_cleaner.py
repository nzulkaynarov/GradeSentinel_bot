import re
from typing import Optional, Tuple

def sanitize_grade(raw_string: str) -> Tuple[Optional[float], Optional[str]]:
    """
    Очищает строковое значение оценки из Google Таблицы.
    
    Args:
        raw_string: Значение из ячейки Google Таблицы (например, "5-", "н", "4.0").
        
    Returns:
        tuple (grade_value, raw_text):
        - grade_value: числовое значение (float), если это точно оценка, или None, если это текст (например, "н", "болел").
        - raw_text: очищенный от лишних пробелов оригинальный текст.
    """
    if not isinstance(raw_string, str):
        raw_string = str(raw_string)
        
    clean_text = raw_string.strip()
    if not clean_text:
        return None, ""
        
    # Ищем базовую оценку от 1 до 5
    # Поддерживаем: "5", "5-", "4+", "4=", "5.0", "4.5" и т.д.
    grade_match = re.match(r'^([1-5])([.\-+=]?\d?)$', clean_text)
    if grade_match:
        base_grade = float(grade_match.group(1))
        return base_grade, clean_text

    # Если это текстовая отметка (отсутствие, болезнь и т.д.)
    if clean_text.lower() in ['н', 'б', 'н/а', 'осв', 'болел', 'болела', 'ув', 'дз', 'см', 'б/ф']:
         return None, clean_text
    
    # В остальных случаях (даты, мусор) возвращаем None, None
    return None, None

# Примеры использования (тесты):
if __name__ == "__main__":
    test_cases = [
        ("5-", (5.0, "5-")),
        ("4+", (4.0, "4+")),
        ("4=", (4.0, "4=")),
        ("3", (3.0, "3")),
        (" 2 ", (2.0, "2")),
        ("н", (None, "н")),
        ("болел", (None, "болел")),
        ("болела", (None, "болела")),
        ("5.0", (5.0, "5.0")),
        ("4.5", (4.0, "4.5")),
        ("осв", (None, "осв")),
        ("ув", (None, "ув")),
        ("отлично", (None, None)),
        ("", (None, "")),
    ]
    
    for input_val, expected in test_cases:
        result = sanitize_grade(input_val)
        assert result == expected, f"Failed for {input_val}: expected {expected}, got {result}"
    print("All tests passed.")
