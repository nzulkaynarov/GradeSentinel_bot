"""AI-подпакет: промпты, Anthropic-клиент, кэш инсайтов.

Выделен из `src/analytics_engine.py` (PR-M1, чистый рефакторинг). Оркестрация
(answer_parent_question, analyze_student_grades, compute_*_insight и т.д.)
остаётся в `src/analytics_engine.py` — он импортирует эти подмодули.

Отдельно от `src/ai_tools.py` (tool-use dispatcher) — это модуль, а не пакет,
имена не конфликтуют (`src.ai` vs `src.ai_tools`).
"""
