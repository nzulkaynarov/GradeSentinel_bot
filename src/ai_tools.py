"""Tool definitions + server-side dispatcher для AI-чата (PR_E2).

AI вызывает tools чтобы получить live данные о подписке, семье и тарифах —
вместо того чтобы редиректить родителя в меню (как делал PR_E1).

Архитектура:
  - Все tools принимают НОЛЬ args от AI. Контекст (family_id, lang)
    передаётся в dispatcher closure server-side. AI не может подменить
    family_id и посмотреть чужую семью.
  - Family_id резолвится из student_id (через get_families_for_student).
    Если ученик в нескольких семьях — берём первую (типичный кейс: 1 семья).
  - Tool descriptions в английском (Anthropic best practice — Claude
    лучше выбирает tool по точному английскому описанию). Результаты —
    в языке родителя (чтобы Claude мог цитировать дословно).

Безопасный fallback: любой tool error возвращает «service temporarily
unavailable» — Claude обработает и ответит без этого факта.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  Tool schemas — то что отдаём Anthropic API
# ════════════════════════════════════════════════════════════

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "get_subscription_status",
        "description": (
            "Get the current subscription status for this parent's family. "
            "Returns whether the subscription is active, when it expires, and "
            "days remaining. Use this when the parent asks 'when does my "
            "subscription expire', 'is my subscription active', 'how much time "
            "do I have left', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_family_members",
        "description": (
            "Get the list of family members (parents and children) for this "
            "parent's family. Returns who is the head of family, the other "
            "parents, and the children with their names. Use this when the "
            "parent asks 'who's in my family', 'who has access to grades', "
            "'list my kids', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_family_pricing",
        "description": (
            "Get the current subscription plans and prices. Returns all available "
            "plans (monthly/quarterly/yearly) with prices in UZS and Telegram "
            "Stars. Use this when the parent asks 'how much does it cost', "
            "'what are the prices', 'show me the plans'. Always call this — "
            "prices may change, don't rely on memorized values."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


# ════════════════════════════════════════════════════════════
#  Localized labels — для красивого вывода tool results
# ════════════════════════════════════════════════════════════

_LABELS = {
    'ru': {
        'family_name': 'Семья',
        'subscription_active': 'Подписка АКТИВНА до {date} (осталось {days} дн.)',
        'subscription_inactive': 'Подписка НЕ активна. Чтобы возобновить — /subscription.',
        'subscription_no_data': 'Подписка ни разу не оплачивалась.',
        'head': 'Глава семьи',
        'parent_role': 'Родитель',
        'children_label': 'Дети',
        'no_children': '(нет добавленных детей)',
        'plan_monthly': 'Помесячно',
        'plan_quarterly': 'На квартал',
        'plan_yearly': 'На год',
        'plan_line': '{name}: {amount} UZS ({stars}⭐) за {months} мес.',
        'error': 'Сервис временно недоступен. Скажи родителю проверить /subscription или /manage_family.',
        'no_family': 'Не удалось определить семью для этого ученика. Скажи родителю что лучше всего открыть /manage_family.',
    },
    'uz': {
        'family_name': 'Oila',
        'subscription_active': 'Obuna AKTIV {date} sanagacha ({days} kun qoldi)',
        'subscription_inactive': 'Obuna AKTIV emas. Yangilash uchun — /subscription.',
        'subscription_no_data': 'Obuna hech qachon to\'lanmagan.',
        'head': 'Oila boshlig\'i',
        'parent_role': 'Ota-ona',
        'children_label': 'Bolalar',
        'no_children': '(qo\'shilgan bolalar yo\'q)',
        'plan_monthly': 'Oylik',
        'plan_quarterly': 'Choraklik',
        'plan_yearly': 'Yillik',
        'plan_line': '{name}: {amount} UZS ({stars}⭐) {months} oy uchun.',
        'error': 'Xizmat vaqtincha mavjud emas. Ota-onaga /subscription yoki /manage_family ochishni ayt.',
        'no_family': 'Bu o\'quvchining oilasini aniqlay olmadik. Ota-onaga /manage_family ochishni ayt.',
    },
    'en': {
        'family_name': 'Family',
        'subscription_active': 'Subscription is ACTIVE until {date} ({days} days left)',
        'subscription_inactive': 'Subscription is NOT active. To renew — /subscription.',
        'subscription_no_data': 'Subscription has never been paid.',
        'head': 'Family head',
        'parent_role': 'Parent',
        'children_label': 'Children',
        'no_children': '(no children added)',
        'plan_monthly': 'Monthly',
        'plan_quarterly': 'Quarterly',
        'plan_yearly': 'Yearly',
        'plan_line': '{name}: {amount} UZS ({stars}⭐) for {months} months.',
        'error': 'Service temporarily unavailable. Tell the parent to open /subscription or /manage_family.',
        'no_family': 'Could not determine the family for this student. Tell the parent to open /manage_family.',
    },
}


def _labels(lang: str) -> Dict[str, str]:
    return _LABELS.get(lang) or _LABELS['ru']


# ════════════════════════════════════════════════════════════
#  Tool implementations
# ════════════════════════════════════════════════════════════

_PLAN_KEY_LABELS = {
    'monthly': 'plan_monthly',
    'quarterly': 'plan_quarterly',
    'yearly': 'plan_yearly',
}


def _format_subscription_status(family_id: int, lang: str) -> str:
    from datetime import date as _date
    from datetime import datetime

    from src.database_manager import get_family_subscription, is_subscription_active
    from src.utils import to_date_str
    lbl = _labels(lang)

    sub = get_family_subscription(family_id)
    if not sub or not sub.get('subscription_end'):
        return lbl['subscription_no_data']

    # psycopg отдаёт subscription_end (TIMESTAMP) как datetime-ОБЪЕКТ, не строку
    # (после миграции на PG). Поддерживаем оба варианта (legacy/тесты дают строку).
    end_val = sub['subscription_end']
    if is_subscription_active(family_id):
        end_dt = None
        try:
            if isinstance(end_val, datetime):
                end_dt = end_val
            elif isinstance(end_val, _date):
                end_dt = datetime(end_val.year, end_val.month, end_val.day)
            elif isinstance(end_val, str):
                end_dt = datetime.fromisoformat(end_val.replace(' ', 'T'))
            days = max(0, (end_dt - datetime.now()).days) if end_dt else '?'
        except (ValueError, TypeError):
            days = '?'
        return lbl['subscription_active'].format(date=to_date_str(end_val), days=days)
    return lbl['subscription_inactive']


def _format_family_members(family_id: int, lang: str) -> str:
    from src.database_manager import get_family_members, get_family_students
    lbl = _labels(lang)

    members = get_family_members(family_id)
    students = get_family_students(family_id)

    lines = []
    head_lines = [m for m in members if m.get('is_head')]
    parent_lines = [m for m in members if not m.get('is_head')]

    for m in head_lines:
        lines.append(f"{lbl['head']}: {m.get('fio', '?')}")
    for m in parent_lines:
        lines.append(f"{lbl['parent_role']}: {m.get('fio', '?')}")

    if students:
        names = ", ".join(s.get('fio', '?') for s in students)
        lines.append(f"{lbl['children_label']}: {names}")
    else:
        lines.append(f"{lbl['children_label']}: {lbl['no_children']}")

    return "\n".join(lines)


def _format_family_pricing(lang: str) -> str:
    from src.handlers.subscription import get_plans
    lbl = _labels(lang)

    plans = get_plans()
    lines = []
    for key in ('monthly', 'quarterly', 'yearly'):
        plan = plans.get(key)
        if not plan:
            continue
        name_key = _PLAN_KEY_LABELS.get(key, 'plan_monthly')
        amount_display = plan.get('amount_display') or str(plan.get('amount', 0) // 100)
        lines.append(lbl['plan_line'].format(
            name=lbl[name_key],
            amount=amount_display,
            stars=plan.get('stars_amount', '?'),
            months=plan.get('months', '?'),
        ))
    if not lines:
        return lbl['error']
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  Dispatcher — то что вызывает analytics_engine
# ════════════════════════════════════════════════════════════

# Cap на количество tool-use итераций в одном вопросе. Защита от
# infinite loop'а если Claude по ошибке решит вызвать tool снова и снова.
MAX_TOOL_ITERATIONS = 5


def dispatch_tool(tool_name: str, tool_input: Dict[str, Any],
                  family_id: Optional[int], lang: str) -> str:
    """Выполняет tool по имени и возвращает строковый результат для Anthropic.

    Возвращает безопасный fallback при любой ошибке — Claude получит строку
    и сможет сформулировать ответ. Никогда не raise — это сломает tool_use loop.
    """
    lbl = _labels(lang)

    if tool_name in ('get_subscription_status', 'get_family_members'):
        if family_id is None:
            logger.warning(f"dispatch_tool({tool_name}): family_id is None")
            return lbl['no_family']

    try:
        if tool_name == 'get_subscription_status':
            return _format_subscription_status(family_id, lang)
        if tool_name == 'get_family_members':
            return _format_family_members(family_id, lang)
        if tool_name == 'get_family_pricing':
            return _format_family_pricing(lang)
        logger.warning(f"dispatch_tool: unknown tool {tool_name!r}")
        return lbl['error']
    except Exception as e:
        logger.warning(f"dispatch_tool({tool_name}) failed: {e}", exc_info=True)
        return lbl['error']


def resolve_family_id_for_student(student_id: int) -> Optional[int]:
    """Резолвит family_id из student_id для контекста tool-use.

    Если ученик в нескольких семьях (редкий кейс) — берём первую. AI всё
    равно работает в контексте одного ученика, поэтому семья определена
    однозначно через этот anchor."""
    from src.database_manager import get_families_for_student
    families = get_families_for_student(student_id)
    if not families:
        return None
    return families[0]['id']
