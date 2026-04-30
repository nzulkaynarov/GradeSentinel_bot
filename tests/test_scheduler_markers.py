"""Тест маркеров scheduler-задач: один и тот же маркер не выполнится дважды."""
import src.schedulers as sched
import src.database_manager as dbm


def _reset_scheduler_state():
    """Сбрасывает in-memory кэш маркеров между тестами."""
    with sched._marker_cache_lock:
        sched._marker_cache.clear()


def test_job_runs_once_per_marker(temp_db, monkeypatch):
    _reset_scheduler_state()
    monkeypatch.setattr(sched, '_bot', object())  # бот не нужен — функция-заглушка

    counter = {'n': 0}

    def fake_func():
        counter['n'] += 1

    sched._run_job_safe('evening', '2026-04-30', fake_func)
    sched._run_job_safe('evening', '2026-04-30', fake_func)
    sched._run_job_safe('evening', '2026-04-30', fake_func)
    assert counter['n'] == 1


def test_marker_persists_across_cache_reset(temp_db):
    """Маркер хранится в БД, поэтому переживает «рестарт» (= clear in-memory cache)."""
    _reset_scheduler_state()

    def fake_func():
        pass

    sched._run_job_safe('morning', '2026-04-30', fake_func)
    # Эмулируем рестарт — чистим только кэш
    _reset_scheduler_state()

    # Проверяем, что маркер всё ещё видим (через БД)
    counter = {'n': 0}

    def counting_func():
        counter['n'] += 1

    sched._run_job_safe('morning', '2026-04-30', counting_func)
    assert counter['n'] == 0  # не должно выполниться повторно


def test_different_markers_run_independently(temp_db):
    _reset_scheduler_state()
    counter = {'n': 0}

    def fake_func():
        counter['n'] += 1

    sched._run_job_safe('quarter', '2026-04-30_12', fake_func)
    sched._run_job_safe('quarter', '2026-04-30_18', fake_func)
    assert counter['n'] == 2
