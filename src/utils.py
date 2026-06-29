import re


def to_date_str(value) -> str:
    """'YYYY-MM-DD' из date/datetime ЛИБО строки. None/пусто → ''.

    psycopg для DATE/TIMESTAMP-колонок отдаёт date/datetime ОБЪЕКТЫ (не строки),
    поэтому потребители, делавшие value[:10] (привычка времён SQLite, где даты
    были TEXT), после миграции на PostgreSQL падали с TypeError. Хелпер принимает
    оба варианта (миграция sqlite→PostgreSQL, 2026-06-29)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:10]
    try:
        return value.isoformat()[:10]
    except AttributeError:
        return str(value)[:10]


def mask_phone(phone: str) -> str:
    """Маскирует телефон до последних 4 цифр для логов: 998901234567 → ***4567."""
    if not phone:
        return "***"
    digits = re.sub(r'\D', '', phone)
    if len(digits) <= 4:
        return "***"
    return f"***{digits[-4:]}"


def clean_student_name(title: str) -> str:
    """
    Чистит название таблицы для красивого вывода.
    Пример: "Дневник Зулькайнаров Заур 8 Orion" -> "Зулькайнаров Заур (8 Orion)"
    """
    if not title:
        return "Ученик"
    
    # 1. Удаляем слово "Дневник" (в любом регистре)
    name = re.sub(r'(?i)дневник', '', title).strip()
    
    # 2. Ищем класс (цифра + слово/буква в конце)
    # Предполагаем, что класс это что-то вроде "8 Orion" или "8Б"
    match = re.search(r'(\d+)\s*(.*)$', name)
    if match:
        class_num = match.group(1)
        class_name = match.group(2).strip()
        
        # Основное имя (все что до цифр)
        main_name = name[:match.start()].strip()
        
        if class_name:
            return f"{main_name} ({class_num} {class_name})"
        else:
            return f"{main_name} ({class_num})"
            
    return name
