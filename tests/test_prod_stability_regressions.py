"""Регрессии для прод-стабильности (июнь 2026).

Покрывает три бага, найденных при аудите прод-логов:
  1. scheduler: джоб 'proactive_alerts' вызывался из _scheduler_loop, но не был
     зарегистрирован в _job_locks → KeyError каждый день в 17:00, фича мертва.
  2. handlers: conversational message-хендлеры матчили сообщения из групп
     (бот добавлен в семейные группы для уведомлений) → AI отвечал/галлюцинировал
     прямо в группе. Должны срабатывать только в private.
  3. google_sheets: сервис должен быть thread-local (httplib2 не thread-safe) —
     общий singleton под ThreadPoolExecutor вызывал SSL-corruption и SIGSEGV.
"""
import re
import inspect
from types import SimpleNamespace

import src.schedulers as sched
import src.google_sheets as gs
from src.handlers import navigation


# ─────────────────────────── 1. scheduler locks ───────────────────────────

def test_every_scheduled_job_has_a_lock():
    """Каждый job, вызываемый _run_job_safe в _scheduler_loop, обязан иметь лок.

    Парсим исходник цикла и сверяем с _job_locks — ловит ЛЮБОЙ будущий
    missing-lock (а не только proactive_alerts)."""
    src_loop = inspect.getsource(sched._scheduler_loop)
    invoked = set(re.findall(r"_run_job_safe\(\s*'([^']+)'", src_loop))
    assert invoked, "не нашли ни одного _run_job_safe — проверь регэксп/код"
    missing = invoked - set(sched._job_locks)
    assert not missing, f"джобы без лока в _job_locks: {missing}"


def test_proactive_alerts_lock_registered():
    assert 'proactive_alerts' in sched._job_locks


# ─────────────────────── 2. private-only message guard ──────────────────────

def _msg(text, chat_type):
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=555, type=chat_type),
        from_user=SimpleNamespace(id=555),
    )


def test_matches_label_ignores_group_messages(monkeypatch):
    """Текст, совпадающий с label кнопки, не должен матчиться в группе."""
    monkeypatch.setattr(navigation, 'get_user_lang', lambda _id: 'ru')
    monkeypatch.setattr(navigation, 't', lambda key, lang: '💬 Чат')

    assert navigation._matches_label(_msg('💬 Чат', 'private'), 'nav_chat') is True
    assert navigation._matches_label(_msg('💬 Чат', 'group'), 'nav_chat') is False
    assert navigation._matches_label(_msg('💬 Чат', 'supergroup'), 'nav_chat') is False


# ───────────────────────── 3. thread-local sheets ──────────────────────────

def test_sheets_service_is_thread_local():
    """get_sheets_service кэшируется в thread-local, не в глобальном singleton."""
    assert hasattr(gs, '_thread_local')
    # глобального общего сервиса быть не должно (старый баг)
    assert not hasattr(gs, '_sheets_service')
