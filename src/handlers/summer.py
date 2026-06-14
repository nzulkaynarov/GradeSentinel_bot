"""«Летний режим» этап 2 — inline-feedback под каникулярными нэджами.

Кнопки под каждой активностью: ✅ Сделали / 🔄 Другую / 🔕 Хватит.
- «🔕 Хватит» = opt-out в 1 тап (settings summer_optout_{tg}); scheduler
  фильтрует таких получателей → ключевой guardrail (не спамить родителя).
- «🔄 Другую» = пере-генерация активности для того же (детерминированного по
  неделе) предмета — AI даёт другую идею.
- «✅ Сделали» = позитивный ack, убираем клавиатуру.

build_summer_keyboard используется и здесь, и в scheduler._check_summer_mode
(единый источник callback_data).
"""
import logging
from datetime import datetime, timezone, timedelta

from telebot import types

from src.bot_instance import bot
from src.i18n import t
from src.notification_helpers import TIMEZONE_OFFSET_HOURS
from src.database_manager import (
    get_user_lang, set_summer_opted_out, get_rotated_weak_subject,
    get_families_for_student, is_member_of_family, get_family_students,
)

logger = logging.getLogger(__name__)


def _iso_week() -> int:
    now = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET_HOURS)
    return now.isocalendar()[1]


def build_summer_keyboard(student_id: int, lang: str) -> types.InlineKeyboardMarkup:
    """Клавиатура feedback под летним нэджем (общая со scheduler'ом)."""
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton(t("summer_btn_done", lang),
                                   callback_data=f"sm_done_{student_id}"),
        types.InlineKeyboardButton(t("summer_btn_other", lang),
                                   callback_data=f"sm_other_{student_id}"),
    )
    kb.add(types.InlineKeyboardButton(t("summer_btn_stop", lang),
                                      callback_data="sm_stop"))
    return kb


def _parse_sid(data: str, prefix: str):
    try:
        return int(data[len(prefix):])
    except (ValueError, TypeError):
        return None


def _caller_in_student_family(tg_id: int, student_id: int) -> bool:
    """Звонящий должен состоять в семье, где есть этот ученик (анти-подмена)."""
    try:
        return any(is_member_of_family(tg_id, fam['id'])
                   for fam in get_families_for_student(student_id))
    except Exception as e:
        logger.debug(f"summer access check failed: {e}")
        return False


def _student_name(student_id: int) -> str:
    try:
        for fam in get_families_for_student(student_id):
            for s in get_family_students(fam['id']):
                if s['id'] == student_id:
                    return s.get('display_name') or s.get('fio') or 'ученик'
    except Exception as e:
        logger.debug(f"summer student name lookup failed: {e}")
    return 'ученик'


@bot.callback_query_handler(func=lambda c: c.data == 'sm_stop')
def on_summer_stop(call):
    """«🔕 Хватит» — opt-out от летних нэджей в один тап."""
    lang = get_user_lang(call.from_user.id)
    set_summer_opted_out(call.from_user.id, True)
    try:
        bot.edit_message_text(t("summer_stopped", lang), call.message.chat.id,
                              call.message.message_id)
    except Exception as e:
        logger.debug(f"summer stop edit failed: {e}")
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith('sm_done_'))
def on_summer_done(call):
    """«✅ Сделали» — ack + убрать клавиатуру."""
    lang = get_user_lang(call.from_user.id)
    try:
        bot.edit_message_reply_markup(call.message.chat.id,
                                      call.message.message_id, reply_markup=None)
    except Exception as e:
        logger.debug(f"summer done edit failed: {e}")
    bot.answer_callback_query(call.id, t("summer_done_ack", lang))


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith('sm_other_'))
def on_summer_other(call):
    """«🔄 Другую» — пере-генерация активности для предмета этой недели."""
    lang = get_user_lang(call.from_user.id)
    sid = _parse_sid(call.data, 'sm_other_')
    if sid is None or not _caller_in_student_family(call.from_user.id, sid):
        bot.answer_callback_query(call.id)
        return

    weak = get_rotated_weak_subject(sid, _iso_week())
    if not weak:
        bot.answer_callback_query(call.id, t("summer_other_failed", lang))
        return

    from src.analytics_engine import generate_summer_activity
    text = generate_summer_activity(_student_name(sid), weak['subject'], lang=lang)
    if not text:
        bot.answer_callback_query(call.id, t("summer_other_failed", lang))
        return

    full_text = f"{t('summer_activity_heading', lang)}\n\n{text}"
    try:
        bot.edit_message_text(full_text, call.message.chat.id, call.message.message_id,
                              reply_markup=build_summer_keyboard(sid, lang))
    except Exception as e:
        logger.debug(f"summer other edit failed: {e}")
    bot.answer_callback_query(call.id)
