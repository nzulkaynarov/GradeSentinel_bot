"""Тесты для пакета PR-J (UX-nav-cleanup).

Покрывают:
- B17: метки reply-keyboard главного меню в ai_chat_mode НЕ уходят в AI.
- get_button_action: атомарный доступ к BUTTON_ACTIONS (item 6).
- Спиннеры: callback_add_child/add_member вызывают answer_callback_query (item 3).
- DEFAULT_PLANS: _process_price_input не мутирует глобальный дефолт (item 8).
- group_cancel не-инициатором: ровно один answer, тост показан (item 7).
- seed_db импортируется без ошибки (item 9).

Тесты не требуют БД — все обращения к БД/боту замоканы.
"""
import os
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

# bot_instance валидирует ':' в BOT_TOKEN (иначе exit(1)). Форсим валидный
# токен ДО импорта bot-зависимых модулей — setdefault недостаточно, т.к. другой
# тест-модуль мог уже выставить бесколоночный "test-token".
if ":" not in os.environ.get("BOT_TOKEN", ""):
    os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")

from src.i18n import t, get_button_action, load_translations  # noqa: E402

load_translations()


def _fake_call(data, user_id=555, chat_id=555, call_id="cbid"):
    return SimpleNamespace(
        id=call_id,
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type="private"),
            message_id=42,
        ),
    )


# ─────────────────────────── item 6: get_button_action ───────────────────────────
def test_get_button_action_maps_menu_labels():
    assert get_button_action(t("btn_grades", "ru")) == "grades"
    assert get_button_action(t("btn_user_menu", "uz")) == "user_menu"
    assert get_button_action(t("btn_grades", "en")) == "grades"


def test_get_button_action_none_for_unknown_and_none():
    assert get_button_action("как дела у ребёнка по алгебре?") is None
    assert get_button_action(None) is None


def test_build_button_actions_rebinds_atomically():
    """_build_button_actions должен присваивать НОВЫЙ объект (rebind), а не
    мутировать существующий in-place — чтобы читатель не поймал промежуточное
    пустое состояние при смене языка."""
    import src.i18n as i18n
    before = i18n.BUTTON_ACTIONS
    i18n._build_button_actions()
    after = i18n.BUTTON_ACTIONS
    assert before is not after  # новый объект
    assert get_button_action(t("btn_grades", "ru")) == "grades"


# ─────────────────────────── B17: ai_chat не ловит метки ─────────────────────────
def _filter_func(handler_name):
    import src.main  # noqa: F401 — регистрирует все хендлеры
    from src.bot_instance import bot
    for h in bot.message_handlers:
        if h["function"].__name__ == handler_name:
            return h["filters"].get("func")
    raise AssertionError(f"handler {handler_name} not registered")


def test_b17_ai_chat_filter_skips_menu_labels():
    """В ai_chat_mode метка «Оценки»/«Меню» НЕ должна матчиться ai_chat-хендлером
    (иначе улетит в AI как вопрос), а обычный текст — должен."""
    ai_filter = _filter_func("_on_chat_message")
    label_msg = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(id=555),
        text=t("btn_grades", "ru"),
    )
    question_msg = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(id=555),
        text="какие оценки за неделю?",
    )
    # Пользователь В ai_chat_mode для обоих сообщений.
    with patch("src.handlers.ai_chat._is_ai_chat_state", return_value=True):
        assert ai_filter(label_msg) is False   # метка — пропускаем дальше
        assert ai_filter(question_msg) is True  # вопрос — ловим в AI


def test_b17_menu_handler_catches_label():
    """Та же метка ДОЛЖНА матчиться handle_menu_buttons (куда она проваливается)."""
    menu_filter = _filter_func("handle_menu_buttons")
    label_msg = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        text=t("btn_grades", "ru"),
    )
    assert menu_filter(label_msg) is True


# ─────────────────────────── item 3: спиннеры ────────────────────────────────────
def test_callback_add_child_answers_callback():
    import src.handlers.family as fam
    fake_bot = MagicMock()
    with patch.object(fam, "bot", fake_bot), \
         patch.object(fam, "_check_family_access", return_value=True), \
         patch.object(fam, "set_user_state"), \
         patch.object(fam, "get_user_lang", return_value="ru"):
        fam.callback_add_child(_fake_call("add_child_7"))
    fake_bot.answer_callback_query.assert_called_once_with("cbid")


def test_callback_add_member_answers_callback():
    import src.handlers.family as fam
    fake_bot = MagicMock()
    with patch.object(fam, "bot", fake_bot), \
         patch.object(fam, "_check_family_access", return_value=True), \
         patch.object(fam, "set_user_state"), \
         patch.object(fam, "get_user_lang", return_value="ru"):
        fam.callback_add_member(_fake_call("add_member_7"))
    fake_bot.answer_callback_query.assert_called_once_with("cbid")


# ─────────────────────────── item 8: DEFAULT_PLANS deepcopy ──────────────────────
def test_get_plans_returns_deepcopy_of_default():
    import src.handlers.subscription as sub
    # get_plans/DEFAULT_PLANS живут в submodule `plans` (PR-M2) — патчим там.
    with patch.object(sub.plans, "get_plans_from_db", return_value=None):
        plans = sub.get_plans()
        plans["monthly"]["amount"] = 1  # мутируем возвращённое
    assert sub.DEFAULT_PLANS["monthly"]["amount"] == 29900_00  # дефолт цел


def test_process_price_input_does_not_mutate_default():
    import src.handlers.subscription as sub
    original_amount = sub.DEFAULT_PLANS["monthly"]["amount"]
    saved = {}
    fake_bot = MagicMock()
    msg = SimpleNamespace(chat=SimpleNamespace(id=555), text="50000")
    # _process_price_input + его зависимости живут в submodule `plans` (PR-M2).
    with patch.object(sub.plans, "bot", fake_bot), \
         patch.object(sub.plans, "get_plans_from_db", return_value=None), \
         patch.object(sub.plans, "save_plans_to_db", side_effect=lambda p: saved.update(p)), \
         patch.object(sub.plans, "get_user_lang", return_value="ru"), \
         patch("src.database_manager.clear_user_state"), \
         patch.object(sub.plans, "send_content"):
        sub._process_price_input(msg, "monthly")
    # Глобальный дефолт не тронут…
    assert sub.DEFAULT_PLANS["monthly"]["amount"] == original_amount
    # …а в БД сохранена новая цена (UZS→тийины).
    assert saved["monthly"]["amount"] == 50000 * 100


# ─────────────────────────── item 7: group_cancel ────────────────────────────────
def test_group_cancel_non_initiator_single_answer_with_toast():
    import src.handlers.group as grp
    fake_bot = MagicMock()
    # data=gcancel_111 (инициатор 111), тапает 222 → не инициатор.
    call = _fake_call("gcancel_111", user_id=222)
    with patch.object(grp, "bot", fake_bot), \
         patch.object(grp, "get_user_lang", return_value="ru"):
        grp.callback_group_cancel(call)
    # Ровно один answer — с текстом-тостом, message НЕ удалён.
    fake_bot.answer_callback_query.assert_called_once()
    args, kwargs = fake_bot.answer_callback_query.call_args
    assert args[0] == "cbid"
    assert t("group_not_inviter", "ru") in args
    fake_bot.delete_message.assert_not_called()


def test_group_cancel_initiator_answers_and_deletes():
    import src.handlers.group as grp
    fake_bot = MagicMock()
    call = _fake_call("gcancel_111", user_id=111)
    with patch.object(grp, "bot", fake_bot), \
         patch.object(grp, "get_user_lang", return_value="ru"):
        grp.callback_group_cancel(call)
    fake_bot.answer_callback_query.assert_called_once_with("cbid")
    fake_bot.delete_message.assert_called_once()


# ─────────────────────────── item 9: seed_db import ──────────────────────────────
def test_seed_db_imports_cleanly():
    import importlib
    import seed_db
    importlib.reload(seed_db)
    assert hasattr(seed_db, "seed")
