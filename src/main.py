import os
import time
import threading
from dotenv import load_dotenv
import logging
from telebot import types

# Load environment variables setup
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Конфиг и rate limiter вынесены в отдельные модули
from src.config import POLLING_INTERVAL, HEARTBEAT_INTERVAL
from src.rate_limiter import is_rate_limited

from src.bot_instance import bot
from src.ui import send_menu_safe
from src.database_manager import init_db, get_parent_by_phone, update_parent_telegram_id, update_parent_first_name, get_greeting_name, get_parent_role, get_user_lang
from src.i18n import load_translations, t
from src.monitor_engine import start_polling

# Import handlers to register them
# state_flows ПЕРВЫЙ — он регистрирует state-machine message_handler'ы которые
# должны обходиться pyTelegramBotAPI'ом раньше generic-обработчиков из других
# модулей. Регистрация в порядке import.
import src.handlers.state_flows  # noqa: F401
# navigation handlers для постоянной reply-keyboard {Чат, Дашборд, Меню}
# регистрируются ПЕРЕД ai_chat — точное совпадение с label-кнопкой
# должно перехватываться здесь, а не уходить как вопрос в AI (PR_F).
import src.handlers.navigation  # noqa: F401
# ai_chat — message_handler по state ai_chat_mode, ловит вопросы родителя.
import src.handlers.ai_chat  # noqa: F401
import src.handlers.admin
import src.handlers.family
import src.handlers.communication
import src.handlers.analytics
import src.handlers.settings
import src.handlers.subscription
import src.handlers.invite
import src.handlers.group
# panel — user-панель, up_* callbacks и generic reply-меню-роутер
# (`handle_menu_buttons`). Импортируется ПОСЛЕДНИМ из handler-модулей: его
# generic func-handler должен регистрироваться после ai_chat/навигации, чтобы
# метки, не пойманные ими, проваливались сюда (см. tests/test_ux_nav_cleanup.py B17).
import src.handlers.panel  # noqa: F401

# For direct routing in main menu
from src.handlers.admin import cmd_admin_panel
# Re-export панель-хелперов. navigation.py и settings.py импортируют часть из
# них через `from src.main import ...` (обратная совместимость), плюс они же
# используются ниже в auth/welcome flow.
from src.handlers.panel import (
    _show_user_panel, _invalidate_panel_cache, _get_panel_data,
    _build_reply_keyboard, _set_dashboard_menu_button, _start_onboarding,
    cmd_user_menu,
)


# ====================
# Telegram bot setup
# ====================
@bot.message_handler(commands=['help'])
def send_help(message):
    """Справка по командам бота — адаптируется под роль пользователя."""
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    from src.database_manager import is_head_of_any_family

    # Базовая справка для всех
    text = t("help_parent", lang)

    # Дополнение для глав семей
    if is_head_of_any_family(user_id):
        text += t("help_head", lang)

    # Дополнение для админа
    if get_parent_role(user_id) == 'admin':
        text += t("help_admin", lang)

    send_menu_safe(user_id, text)


@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    admin_id_env = os.environ.get("ADMIN_ID")

    # Проверяем deep link (инвайт)
    args = message.text.split(maxsplit=1)

    # S3: троттлинг deeplink-диспатча (inv_/ai_) — защита от перебора
    # инвайт-кодов и спама AI-чатом через /start <payload>.
    if len(args) > 1 and (args[1].startswith('inv_') or args[1].startswith('ai_')):
        if is_rate_limited(user_id):
            bot.send_message(user_id, t("rate_limited", lang))
            return

    if len(args) > 1 and args[1].startswith('inv_'):
        invite_code = args[1][4:]
        from src.handlers.invite import handle_invite_deeplink
        handle_invite_deeplink(message, invite_code)
        return

    # Deep-link AI: /start ai_<base64(question)> — переход из WebApp
    # дашборда. Open chat в ai_chat_mode + opt. pre-filled question.
    if len(args) > 1 and args[1].startswith('ai_'):
        from src.handlers.ai_chat import handle_ai_deeplink
        handle_ai_deeplink(message, args[1][3:])
        return

    # Автоматическая авторизация админа.
    # PR_F-hotfix: admin НЕ попадает в AI-чат как default — он работает с
    # admin panel. Чистим ai_chat_mode state если случайно туда зашёл,
    # шлём приветствие + кнопку «🛠 Управление».
    if admin_id_env and str(user_id) == str(admin_id_env):
        update_parent_telegram_id(f"admin_{user_id}", user_id)
        update_parent_first_name(user_id, message.from_user.first_name)
        _show_admin_welcome(user_id, lang)
        return

    # Check if user is already saved
    from src.database_manager import is_head_of_any_family, has_children_for_grades, clear_user_state, get_user_state
    role = get_parent_role(user_id)
    if role:
        update_parent_first_name(user_id, message.from_user.first_name)

        # PR_F-hotfix: admin в БД (не env) тоже идёт в admin panel, не в AI-чат.
        if role == 'admin':
            _show_admin_welcome(user_id, lang)
            return

        # /start всегда чистит ai_chat_mode для не-админов — escape hatch
        # если юзер застрял в чате.
        st = get_user_state(user_id)
        if st and st.get('state') == 'ai_chat_mode':
            clear_user_state(user_id)

        if not is_head_of_any_family(user_id) and not has_children_for_grades(user_id):
            send_menu_safe(user_id, t("auth_not_linked", lang, btn_support=t("btn_support", lang)))
            return

        # Pin Dashboard button: ставим chat menu button (слева от input)
        # как «📊 Открыть дашборд» — постоянный доступ к WebApp.
        # Только для родителей с детьми (admin'у не нужно).
        if has_children_for_grades(user_id):
            _set_dashboard_menu_button(user_id, lang)

        # PR_F: авторизованный юзер с детьми → СРАЗУ в AI-чат, не в меню.
        if has_children_for_grades(user_id):
            _enter_default_chat(user_id)
        else:
            _show_user_panel(user_id)
        return


@bot.callback_query_handler(func=lambda call: call.data.startswith('start_lang_'))
def callback_start_lang(call):
    """Выбор языка при первом /start — сохраняем и показываем авторизацию."""
    lang = call.data.replace('start_lang_', '')
    user_id = call.message.chat.id

    # Для неавторизованных — пока не можем сохранить в БД (нет записи parents),
    # запоминаем в user_states как временное
    from src.database_manager import set_user_state
    set_user_state(user_id, "pending_lang", lang)

    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(user_id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete start_lang message: {e}")

    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True,
                                        input_field_placeholder=t("auth_placeholder", lang))
    button = types.KeyboardButton(t("btn_share_contact", lang), request_contact=True)
    markup.add(button)

    bot.send_message(user_id, t("auth_welcome", lang), reply_markup=markup)


@bot.message_handler(content_types=['contact'])
def contact_handler(message):
    if message.contact is not None:
        phone = message.contact.phone_number
        user_id = message.chat.id

        # Проверяем состояние пользователя (язык или инвайт)
        from src.database_manager import get_user_state, clear_user_state, set_user_lang
        state = get_user_state(user_id)
        chosen_lang = state.get('data') if state and state.get('state') == 'pending_lang' else None
        pending_invite = state.get('data') if state and state.get('state') == 'pending_invite' else None

        if chosen_lang:
            clear_user_state(user_id)

        # Если это инвайт — обрабатываем через invite handler
        if pending_invite:
            clear_user_state(user_id)
            from src.handlers.invite import process_invite_after_contact
            if process_invite_after_contact(user_id, phone, pending_invite):
                return

        parent = get_parent_by_phone(phone)

        if parent:
            update_parent_telegram_id(phone, user_id)
            update_parent_first_name(user_id, message.from_user.first_name)
            # Перечитываем после update — приветствие использует свежее имя
            parent = get_parent_by_phone(phone) or parent

            # Сохраняем выбранный язык (если выбран при /start)
            if chosen_lang:
                set_user_lang(user_id, chosen_lang)
            lang = chosen_lang or get_user_lang(user_id)

            role = parent.get('role', 'senior')
            greeting_name = get_greeting_name(parent)
            from src.database_manager import is_head_of_any_family, has_children_for_grades

            if role != 'admin' and not is_head_of_any_family(user_id) and not has_children_for_grades(user_id):
                welcome_msg = t("auth_not_linked_contact", lang, name=greeting_name)
            else:
                welcome_msg = t("auth_success", lang, name=greeting_name)
                if role == 'admin':
                    welcome_msg += t("auth_role_admin", lang)
                elif is_head_of_any_family(user_id):
                    welcome_msg += t("auth_role_head", lang)
                else:
                    welcome_msg += t("auth_role_parent", lang, btn_grades=t("btn_grades", lang))

            send_menu_safe(user_id, welcome_msg)
            from src.utils import mask_phone
            logger.info(f"User {mask_phone(phone)} authorized as {role}")

            # Запускаем onboarding wizard если юзер новый (никогда не получал
            # /start раньше — определяем по отсутствию записи в user_states
            # с ключом onboarding_done)
            from src.database_manager import get_user_state, set_user_state
            onb_state = get_user_state(user_id)
            if not (onb_state and onb_state.get('state') == 'onboarding_done'):
                set_user_state(user_id, 'onboarding_done', '1')
                _start_onboarding(user_id, greeting_name)
        else:
            lang = chosen_lang or 'ru'
            # Self-serve путь вместо тупика: юзер не в БД → две кнопки
            # «Создать свою семью» и «У меня инвайт-ссылка». Админу
            # больше не нужно регистрировать каждого вручную.
            inline_markup = types.InlineKeyboardMarkup()
            inline_markup.add(types.InlineKeyboardButton(
                t("btn_create_my_family", lang), callback_data="up_create_family_new"
            ))
            inline_markup.add(types.InlineKeyboardButton(
                t("btn_have_invite", lang), callback_data="up_have_invite"
            ))
            admin_id_env = os.environ.get("ADMIN_ID")
            if admin_id_env:
                inline_markup.add(types.InlineKeyboardButton(
                    t("btn_contact_admin", lang),
                    url=f"tg://user?id={admin_id_env}"
                ))
            # Сначала убираем reply-клавиатуру (request_contact не нужна).
            # Используем служебное «...» которое сразу удаляем, чтобы не оставлять
            # пустое сообщение в чате. Затем основное сообщение с inline-кнопками.
            # ВАЖНО: раньше посылали " " (пробел) — Telegram теперь возвращает 400
            # "text must be non-empty", из-за чего кнопки не появлялись и юзер
            # видел тупик с одним только вопросом «Что хотите сделать?».
            try:
                kbd_remover = bot.send_message(
                    user_id, "…", reply_markup=types.ReplyKeyboardRemove()
                )
                bot.delete_message(user_id, kbd_remover.message_id)
            except Exception as e:
                logger.debug(f"Could not remove reply keyboard: {e}")
            bot.send_message(user_id, t("auth_phone_not_found", lang),
                              reply_markup=inline_markup)

            # Сохраняем телефон чтобы при выборе "Создать семью" сразу зарегать
            from src.database_manager import set_user_state
            set_user_state(user_id, "pending_selfserve_phone", phone)

            from src.utils import mask_phone
            logger.warning(f"Unauthorized access attempt from phone: {mask_phone(phone)}")
    else:
        lang = get_user_lang(message.chat.id)
        bot.send_message(message.chat.id, t("auth_contact_error", lang))


# ═══════════════════════════════════════════
#  Admin welcome + вход в parent-режим (AI-чат)
# ═══════════════════════════════════════════

def _show_admin_welcome(chat_id: int, lang: str):
    """Приветствие админа: текст + reply-keyboard admin-режима
    {🛠 Управление, 👨 Я родитель}. Toggle между режимами — через эту
    постоянную reply-keyboard (PR role-toggle 21.05).

    Admin тапает «🛠 Управление» → admin panel; «👨 Я родитель» → parent
    режим (только если есть дети). Inline-кнопки из welcome убраны
    (дублировали label'ы)."""
    from src.database_manager import clear_user_state, get_user_state
    st = get_user_state(chat_id)
    if st and st.get('state') == 'ai_chat_mode':
        clear_user_state(chat_id)

    bot.send_message(chat_id, t("auth_admin_welcome", lang),
                      reply_markup=_build_reply_keyboard(lang, mode='admin'),
                      parse_mode='HTML')


def _enter_default_chat(chat_id: int):
    """Стартует parent-режим (AI-чат + reply-keyboard) для любого юзера.

    PR role-toggle: если юзер — admin (через «👨 Я родитель» из admin
    режима), reply-keyboard содержит дополнительный toggle «🛠 Управление»
    для возврата обратно. Не-admin видит только {💬 Чат, ⚙️ Меню}."""
    from src.handlers.ai_chat import start_ai_chat_with_keyboard
    lang = get_user_lang(chat_id)
    is_admin = get_parent_role(chat_id) == 'admin'
    keyboard = _build_reply_keyboard(lang, mode='parent', is_admin=is_admin)
    start_ai_chat_with_keyboard(chat_id, keyboard)


@bot.callback_query_handler(func=lambda call: call.data == 'open_admin_panel')
def callback_open_admin_panel(call):
    """Inline-кнопка из admin welcome → открывает admin panel."""
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete admin welcome: {e}")
    cmd_admin_panel(call.message)


@bot.callback_query_handler(func=lambda call: call.data == 'open_admin_as_parent')
def callback_open_admin_as_parent(call):
    """«👨 Я родитель» — admin переключается в parent-режим про своих детей.

    Tier 2 admin-audit: owner проекта = admin с собственными детьми, и ему
    нужно тестировать parent UX (AI-чат, дашборд) без второго аккаунта.
    Возврат в admin panel через /start (он снова покажет admin welcome)."""
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete admin welcome: {e}")
    _enter_default_chat(call.from_user.id)


# ═══════════════════════════════════════════
#  Heartbeat + polling + entrypoint
# ═══════════════════════════════════════════

_HEARTBEAT_PATH = os.environ.get(
    "HEARTBEAT_PATH",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", ".heartbeat"
    ),
)


def _heartbeat_loop():
    """Раз в N секунд touch'ит файл data/.heartbeat. Docker healthcheck смотрит mtime —
    если файл «протух» (>3 минут без обновлений), значит main thread/polling завис.
    Интервал берётся из config.HEARTBEAT_INTERVAL."""
    while True:
        try:
            os.makedirs(os.path.dirname(_HEARTBEAT_PATH), exist_ok=True)
            with open(_HEARTBEAT_PATH, "w") as f:
                f.write(str(int(time.time())))
        except Exception as e:
            logger.warning(f"Heartbeat write failed: {e}")
        time.sleep(HEARTBEAT_INTERVAL)


def start_bot():
    """Запускает Telegram бота в режиме polling.

    Обёртка вокруг bot.polling с резистентностью к сетевым ошибкам.
    none_stop=True не покрывает крэши polling-thread'а (timeout, DNS, 502) —
    они всплывают через polling_thread.raise_exceptions() и убивают процесс.
    За 7д до фикса наблюдалось 82 traceback'а (≈12/день), каждый = systemd
    restart на 4 сек + потеря notification window. Сейчас retry-loop внутри.
    """
    logger.info("Starting Telegram Bot...")
    threading.Thread(target=_heartbeat_loop, daemon=True).start()

    backoff = 5
    max_backoff = 120
    while True:
        try:
            bot.polling(none_stop=True, timeout=30, long_polling_timeout=30)
            backoff = 5  # успешный exit (manual stop) — сброс
            break
        except Exception as e:
            logger.warning(
                f"bot.polling crashed: {type(e).__name__}: {e}. "
                f"Restarting in {backoff}s (in-process, no systemd restart)."
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

def main():
    logger.info("Initializing GradeSentinel v2.0...")

    # 0. Error reporter (Sentry hook, no-op без SENTRY_DSN)
    from src.error_reporter import _try_init_sentry
    _try_init_sentry()

    # 1. Init DB
    init_db()

    # 2. Load translations
    load_translations()

    # 3. Init unified Sender (notification layer with retry + quiet hours)
    from src.notifications import init_sender
    init_sender(bot)

    # 4. Start monitor engine in a separate thread
    from src.monitor_engine import set_bot_instance
    set_bot_instance(bot)

    monitor_thread = threading.Thread(target=start_polling, args=(POLLING_INTERVAL,), daemon=True)
    monitor_thread.start()
    logger.info("Monitor engine thread started with bot integration.")

    # 4. Start weekly AI report scheduler (if API key available)
    from src.handlers.analytics import start_weekly_scheduler
    start_weekly_scheduler()

    # 5. Start daily schedulers (evening summary, quiet hours flush, bot alive)
    from src.schedulers import start_daily_schedulers, set_bot_instance as set_scheduler_bot
    set_scheduler_bot(bot)
    start_daily_schedulers()

    # 6. Register bot commands in Telegram menu
    _register_bot_commands()

    # 7. Start telegram bot blocking main thread
    start_bot()


def _register_bot_commands():
    """Регистрирует команды бота в меню Telegram (кнопка / в чате).

    Разные scope для приватных и групповых чатов:
    - в личке: /start /help /grades /status;
    - в группе: /set_thread /unlink_group (управление привязкой к семье).
    Без scope-разделения в группе подсказывались бы /grades и /status, которые там не работают."""
    try:
        # Commands cleanup: оставили минимум полезных. Раньше показывали
        # 6 команд (включая /status и /ai_report), родители путались.
        # Now: только то что родитель реально использует. /status (admin
        # stats) и /ai_report (можно из дашборда / AI чата) — убраны из
        # меню, продолжают работать как handler'ы для тех кто знает.
        user_commands = [
            types.BotCommand("start", "Главное меню"),
            types.BotCommand("help", "Как пользоваться"),
            types.BotCommand("grades", "Оценки за сегодня"),
            types.BotCommand("subscription", "Подписка"),
        ]
        bot.set_my_commands(user_commands)
        bot.set_my_commands(
            user_commands,
            scope=types.BotCommandScopeAllPrivateChats(),
        )

        # Group chats — управление привязкой к семье
        bot.set_my_commands(
            [
                types.BotCommand("set_thread", "Указать тему по ссылке"),
                types.BotCommand("unlink_group", "Отвязать чат от семьи"),
            ],
            scope=types.BotCommandScopeAllGroupChats(),
        )

        logger.info("Bot commands registered (private + group scopes).")
    except Exception as e:
        logger.warning(f"Could not set bot commands: {e}")

if __name__ == '__main__':
    main()
