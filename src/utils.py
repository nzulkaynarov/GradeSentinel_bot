import re

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
