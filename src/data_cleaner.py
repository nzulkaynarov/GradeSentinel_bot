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
    # Шаблон ловит только если строка начинается с цифры 1-5 и имеет длину до 2-3 символов (напр "5", "5-", "4+")
    # Или просто проверяем, что вся строка — это по сути оценка.
    if len(clean_text) <= 3 and re.match(r'^[1-5][\-+=]?$', clean_text):
        match = re.search(r'([1-5])', clean_text)
        if match:
            return float(match.group(1)), clean_text
            
    # Если это "н", "б" и т.д. — возвращаем только текст, считая это "отметкой"
    if clean_text.lower() in ['н', 'б', 'н/а', 'осв']:
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
        ("5.0", (5.0, "5.0")),
        ("отлично", (None, "отлично"))
    ]
    
    for input_val, expected in test_cases:
        result = sanitize_grade(input_val)
        assert result == expected, f"Failed for {input_val}: expected {expected}, got {result}"
    print("All tests passed.")
