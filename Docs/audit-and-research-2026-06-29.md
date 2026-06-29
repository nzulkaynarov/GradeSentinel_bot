# GradeSentinel - Аудит + ресерч мировой практики (2026-06-29)

> Сырой результат 4-агентного разбора (техлид+продукт+2x ресерч). Синтез/план - в Docs/refactor-and-product-plan-2026-06-29.md


---

## Часть 1 — Техлид-аудит (архитектура, код, надёжность, тесты, масштаб)

I have enough to produce the audit. Here it is.

---

# TECH-LEAD AUDIT — GradeSentinel_bot (2026-06-29)

Scope: full read of `src/`, `webapp/`, `tests/`, `deploy/`, CI. Codebase ~6k LOC `src/` + 2k `webapp/`, 46 test files. Just migrated SQLite→PostgreSQL (psycopg3 + pool over WireGuard). Sync long-polling bot, single bare-metal VPS, self-hosted CI runner.

Overall: unusually mature for a solo bot (systemd hardening, heartbeat, retry/backoff, dedup defense-in-depth, idempotent scheduler markers, thread-local Sheets clients). The real risks are **structural single-instance coupling**, **deploy/migration safety**, and **god-modules with business logic in handlers**.

---

## 1. Architecture risks

**[HIGH] Correctness is welded to a single process via in-memory state.** The two-phase "pending grades" anti-ghost mechanism lives in a module dict (`src/monitor_engine.py:63 _pending_grades`, lock at `:59`), as do `_student_failure_counts`/`_last_failure_alert` (`:40,:42`), `_last_all_grades_sync_ts` (`:621`), the panel cache (`src/main.py:23`), rate-limiter store (`src/rate_limiter.py:25`), and scheduler marker cache (`src/schedulers.py:67`). Consequences: (a) any restart drops pending → 5–10 min notification delay (documented at `:57`); (b) **two instances can never run** — they would double-send notifications and split the pending set. This is the binding constraint on every scaling/HA option below.
Remediation: move shared state to Postgres or Redis. A `pending_grades` table (content-key already matches DB identity) makes the two-phase check survive restarts and unlocks multi-instance. Lowest-effort first step that pays back on both reliability and scaling.

**[HIGH] Auto-migrations on startup + simultaneous bot/webapp restart = concurrent `alembic upgrade`.** Schema is applied lazily at process start: `init_db()` → `apply_migrations()` (`src/database_manager.py:86-93`), called by both `src/main.py:984` and `webapp/app.py:67`. `deploy.yml` restarts `gradesentinel-bot` and `gradesentinel-webapp` in the same step with no ordering, so two processes can run `alembic upgrade head` against the same DB concurrently, and a bad migration ships to prod with zero review and no pre-migration backup.
Remediation: make migrations an explicit, single, gated step in `deploy.yml` (`venv/bin/alembic upgrade head`) that runs once before restarts and after a `pg_dump`; remove `apply_migrations()` from the webapp startup path (keep it only in a deploy step or guard with an advisory lock).

**[MED-HIGH] Every user interaction now has a network hop (WireGuard → DB-VPS) that didn't exist with SQLite.** The pool (`src/db/pg.py:110`) is configured sensibly (`check_connection`, `max_lifetime=1800`, `connect_timeout=20`), but there is no circuit breaker or graceful "DB unavailable, try later" UX — handlers will block up to ~20s then raise. A WireGuard blip stalls polling cycles and freezes handler responses.
Remediation: lower `DB_CONNECT_TIMEOUT`/pool `timeout` for interactive paths, add a thin retry-once-then-friendly-error wrapper, and surface DB-down as an admin alert (you already have the Sender + `_track_ai_outcome` pattern to copy).

**[MED] Polling + Google Sheets as system-of-record.** 5-min `POLLING_INTERVAL` (`src/config.py:32`) means inherent latency and an N-students × reads/cycle load against Sheets quota; handled defensively (429/503 backoff in `src/google_sheets.py`, overlap guard `src/monitor_engine.py:285`, failure-streak admin alert `:211`). Acceptable now; it is a latency/quota ceiling, not a correctness bug. Webhooks for Telegram are orthogonal but would remove the polling thread crash surface (`start_bot` retry loop, `src/main.py:949`).

**[MED] Self-hosted CI runner on the prod VPS is a SPOF and a blast-radius problem.** `runs-on: self-hosted` (`deploy.yml`) on a multi-tenant box shared with the B2B project; the runner has passwordless sudo and writes all prod secrets. Runner/host compromise = full secret disclosure. No staging environment; `push to main` → live.
Remediation: at minimum isolate the runner (dedicated user already done), add a manual-approval environment gate for `main`, and consider an ephemeral cloud runner that SSH-deploys rather than executing on the target.

---

## 2. Code health

**[MED] God-modules.** `wc -l`: `webapp/app.py` 1326, `src/handlers/subscription.py` 1342, `src/main.py` 1056, `src/analytics_engine.py` 1018, `src/schedulers.py` 912, `src/handlers/family.py` 786, `src/monitor_engine.py` 682, `webapp/pdf_export.py` 663. Several exceed any reasonable module budget.
Remediation: split `subscription.py` (payments vs. plan display vs. admin card-confirm — 31 `@bot` handlers, grep confirmed) and `main.py` (auth/contact flow, user-panel callbacks, onboarding, menu router are four separable concerns). `analytics_engine.py` mixes 5 distinct AI features + cache helpers.

**[MED] Business logic lives in handlers; there is no service layer.** `database_manager.py` is a pure re-export facade (368 lines, mostly `from src.db.* import …`) — it is a data-access aggregator, not a service boundary. Payment side-effects run inline in callbacks: `record_payment(...)` / `extend_subscription` are called directly inside `callback_admin_confirm_card` (`src/handlers/subscription.py:520`) and invoice/card flows. State transitions, notifications, and DB writes are interleaved in Telegram callbacks, making them hard to unit-test and to reuse from the webapp.
Remediation: introduce `src/services/` (payments, subscriptions, grades) holding the transactional logic; handlers become thin (authorize → validate → call service → reply).

**[MED] Shadowing in the DB facade is a footgun.** `database_manager.py` re-exports `get_today_grades_for_student` etc. from `src/db/grades.py` (line ~110) and then **redefines** the same names locally at `:176, :199, :224` ("Локальные определения переопределяют одноимённые re-export'ы"). Two functions with the same name, the second silently winning, is exactly how a future edit lands a bug. Pick one home (these Tashkent-window queries belong in `db/grades.py`).

**[MED] Timezone arithmetic is hardcoded and duplicated.** `interval '5 hours'` (Tashkent UTC+5, no DST) is embedded in raw SQL across `database_manager.py:188,212,233,252` and `db/grades.py`. Correct today, but spread across many strings; one missed site = off-by-a-day grades.
Remediation: centralize the "Tashkent today/overnight/yesterday" boundary computation (a single SQL fragment or a Python-computed bound passed as a parameter); `TIMEZONE_OFFSET_HOURS` already exists in config but the SQL ignores it.

**[LOW-MED] Broad exception swallowing.** 127 `except Exception` across `src/`+`webapp/` (only one literal bare `except:`, and it's a comment). Many are legitimately defensive (`bot.delete_message` wrapped + `logger.debug`, all over `main.py`), but the `except Exception: logger.debug(...)` pattern can hide real Telegram/API failures. The AI path is good (specific `APITimeoutError`/`APIError` then a wrapping catch, `analytics_engine.py:108-115`). Tighten the highest-traffic swallows to specific exceptions; keep debug-logging but bump to warning where a failure is actionable.

**Positives worth keeping:** thread-local Sheets service (`google_sheets.py:36`, prevents httplib2 SSL corruption under the worker pool), multiset grade diff (`monitor_engine.py:90`), `_dedup_preserve_order` defense-in-depth (`schedulers.py:252`), and the `sqlite3.Row`-compatible `Row` shim (`db/pg.py:58`) that kept the migration blast radius small.

---

## 3. Reliability / Ops

**[HIGH] Off-site backup is almost certainly OFF, and the data is real PII.** `deploy/gradesentinel-db-backup.sh` gives a daily `pg_dump -Fc` with 14-day rotation on the DB-VPS — good. But `deploy/offsite-backup.sh` **soft-skips (`exit 0`) until manually provisioned** (`/etc/gradesentinel/offsite-backup.env` + rclone). If not configured, loss of the DB-VPS = total data loss, and backups sit on the same box they protect. The DB holds parent phones, children's names, and grades.
Remediation: provision the rclone **crypt** remote now (the script even warns about it); add a monthly automated **restore-test** (restore the latest dump into `railtech_test`-style scratch DB, assert row counts) — an untested backup is a hope, not a backup.

**[MED] Observability has blind spots.** You have: file heartbeat (`main.py:935`) + systemd healthcheck, webapp `/health` (smoke-checked in deploy), optional Sentry, sheet-stuck admin alert, and AI-failure admin alert (`schedulers.py:104`). Missing: there is **no alert if the polling cycle silently stalls** while the process stays up (heartbeat covers thread death, not a wedged DB pool that keeps the loop "running"), no metrics (cycle duration, grades/cycle, queue depth, Sheets 429 rate), and the bot process itself has no health endpoint (only webapp does).
Remediation: emit a "last successful monitor cycle" timestamp to DB and alert if it ages out; export the counters you already log (`ai_calls`, `total_sent`, sheet failures) somewhere queryable.

**[MED-HIGH] Deploy safety.** `push main` auto-deploys, `Restart=always`, `StartLimitBurst=5/300s` — fine for crash recovery, but: no staging, no canary, migrations auto-run (see §1), and every deploy drops in-memory pending. `MemoryMax=400M` on the bot (`gradesentinel-bot.service`) on a shared 4GB box — under a large fan-out cycle this can OOM-kill mid-notification; verify headroom.
Remediation: explicit migrate step + pre-migrate dump; consider `MemoryHigh` tuning after measuring real RSS.

**[LOW] Secrets / smells.** Secrets flow GH Actions → `/etc/gradesentinel/bot.env` (0640 root:gradesentinel) — reasonable. `gradesentinel-bot.service` still carries a stale `Environment="DATABASE_PATH=/var/lib/gradesentinel/sentinel.db"` from the SQLite era (harmless — `DATABASE_URL` wins — but delete it to avoid confusion). All eggs in the GH-secrets + shared-runner basket (see §1).

---

## 4. Test coverage gaps

46 test files, PG-backed, session-scoped Alembic schema + per-test TRUNCATE (`tests/conftest.py`). Strong coverage of the dangerous monitor paths: `test_monitor_pending.py`, `test_history_dedup_race.py`, `test_read/write_path_grade_date.py`, `test_morning_flush_dedup.py`, dashboard/AI/streaming. CI runs against ephemeral `postgres:17` (`tests.yml`).

Gaps, by risk:
- **[HIGH] Payments untested.** No test exercises `subscription.py` invoice/`record_payment`/admin card-confirm/reject or `_check_user_can_pay_for_family` (`:232`, the authz guard on payment callbacks). Money + authz with zero tests is the worst combination here. Only `test_promo_codes.py` touches the area.
- **[MED] WebApp auth/IDOR untested.** `validate_init_data` (`webapp/app.py:79`) and `_authorize_student_access` (`:128`, the ownership + subscription gate) have no dedicated test. Note: `validate_init_data` verifies the HMAC but does **not check `auth_date` freshness** (confirmed: no `auth_date` reference in `app.py`) → captured initData is replayable indefinitely. Add a freshness window and a test for it.
- **[MED] `main.py` routing/onboarding/self-serve family creation** (the large callback surface) is untested.
- **[MED] Concurrency:** pending two-phase logic is tested functionally but not under true parallel `ThreadPoolExecutor` contention.
- **[LOW] False-green risk:** `conftest.py` `pytest.skip`s ALL DB tests when `DATABASE_URL`/`PGHOST` is unset — locally easy to think you're green when you ran ~nothing. CI sets it, so CI is safe, but document/guard for local runs.

---

## 5. Scaling ceiling

- **Hard cap: one instance** (see §1 in-memory state). Horizontal scale is impossible without externalizing `_pending_grades`, rate-limiter, and panel cache. This is the single most important architectural decision to make before growth.
- **Cycle-time vs. interval:** monitor reads N students with `FETCH_WORKERS=8` every 300s plus an hourly full re-sync (`_maybe_sync_all_grades`, `monitor_engine.py:625`). The overlap guard (`:285`) means once a cycle exceeds 5 min, cycles start getting skipped → notification latency silently degrades. Watch cycle duration as the metric that signals the wall (~low thousands of students, depending on Sheets latency).
- **Sheets quota:** read-heavy; 429 handling exists but quota is a per-project ceiling you'll hit before CPU/DB do.
- **DB connections:** bot pool `max_size=5`; webapp under gunicorn = workers × 5. With PG default `max_connections≈100` and a shared DB-VPS, set pool sizes deliberately and cap gunicorn workers.
- **Resource:** 4GB VPS shared with the B2B project; bot capped at 400M; PDF generation (`webapp/pdf_export.py`, reportlab) runs in the webapp process and is the spikiest memory consumer.

Path to scale (in order): (1) externalize pending+state to DB/Redis → unlocks multi-instance; (2) Telegram webhooks behind Caddy → removes polling thread + enables N stateless bot workers; (3) split the monitor into its own worker so user-facing handlers and the Sheets crawler scale independently.

---

### Top 5 to action first
1. Externalize `_pending_grades` (and rate-limit/panel state) to Postgres — fixes restart data-loss AND the scaling cap. (`monitor_engine.py:63`)
2. Make Alembic migrations an explicit, single, backed-up deploy step; stop auto-running them from two processes at restart. (`deploy.yml`, `database_manager.py:86`, `webapp/app.py:67`)
3. Provision + verify off-site **encrypted** backups and a restore drill. (`deploy/offsite-backup.sh`)
4. Add tests for the payment flow and the webapp authz/IDOR + `auth_date` freshness. (`handlers/subscription.py`, `webapp/app.py:79,128`)
5. Add a "last successful monitor cycle" watchdog + DB-down admin alert. (`monitor_engine.py`, `schedulers.py`)

Note: AI calls use a valid current model (`claude-haiku-4-5`) with a 30s timeout and specific-exception handling (`analytics_engine.py`) — no action needed there beyond the existing AI-failure admin alerting.

---

## Часть 2 — Продуктовый аудит (PM)

Confirmed the key finding. Now producing the audit.

---

# GradeSentinel — Product Audit (PM lens)

Scope: read-only review of `Docs/{Project_overview,Terms_of_reference,CONTEXT}.md`, `src/main.py`, `src/handlers/*`, `src/db/families.py`, `src/db/payments.py`, `src/locales/ru.json`. Product (not code) findings. Each finding has impact + a concrete improvement.

Bottom line: the bot is technically mature (retention nudges, proactive alerts, summer mode, multilingual, dashboard already shipped), but the **product rests on one fragile assumption — that a parent can obtain and grant the bot access to a teacher-maintained Google Sheet of grades.** That single dependency drives almost every weakness below and is also the real reason the project is quietly drifting from B2C-family to B2B-school.

---

## 1. End-to-end journey & where it leaks

**The happy path (reconstructed from `main.py` + `family.py`):**
1. `/start` → choose language → "Share contact" (`request_contact=True`).
2. Phone lookup in `parents`. Three outcomes now exist (good — the old hard dead-end is fixed): found → authorized; not found → self-serve buttons ("Create my family" / "I have an invite" / "Contact admin"); invite deep-link → auto-attach.
3. Head creates family → **"Add child" → paste Google Sheet URL** → bot reads sheet title, validates 403/404, creates student, background-imports history + a free first AI analysis.
4. Monitor polls every 5 min, two-phase confirms grades, pushes notifications.

**Where it leaks, in order of severity:**

- **The Google Sheet URL step is the activation cliff (impact: HIGH).** For this to work the parent must (a) know the gradebook lives in Google Sheets, (b) have the share link, and (c) ensure the bot's service-account email (`child_no_access_error` literally says "open Share → add the bot email from credentials.json as Reader"). In practice the **teacher**, not the parent, owns that sheet. A parent who only has view access often can't add a new editor/reader, and most UZ schools use **kundalik.com** (the national e-diary), not ad-hoc Google Sheets. So realistically activation only completes when a teacher is already cooperating — i.e. the "B2C parent self-serve" funnel is fictional for the median user.
  - **Improvement:** Reframe onboarding around the school, not the parent. Ship a one-page "Teacher setup" flow (a template Google Sheet + a 4-step "add this email as Editor" guide with a screenshot, surfaced as a deep link the parent forwards to the teacher). Medium-term: build a kundalik.com / Google Classroom importer so the parent isn't the integration point at all. Until then, instrument the funnel: log how many `add_child` attempts hit `child_no_access_error` vs success — that ratio is your single most important activation metric and it is currently invisible.

- **"Share contact" gate adds friction but buys little (impact: MED).** Phone verification is sold as the security model (TOR §5.1), yet self-serve registration now lets anyone create a family and attach any sheet they can read — so the phone wall no longer gates access to children's data, it only gates entry. It's a step that costs conversion without its original payoff.
  - **Improvement:** Keep contact-share (it dedupes parents and powers invites), but move it *after* the value is shown — let a new user see the sheet-connect/value prop first, request contact only when creating a family or accepting an invite.

- **Silent post-add uncertainty (impact: MED).** After "Add child," history import + free AI run in a background thread (`_bg_import`). If the sheet format is unusual, the parent gets `child_added` but no grades and no notification — and there is no "we're scanning, check back" state nor a failure message to the user (errors only hit logs).
  - **Improvement:** Send a deterministic "Connected ✓ — first scan in progress, I'll message you within X min" and, on import returning 0 rows, a friendly "I couldn't find grades in the expected tabs — is this the right sheet?" instead of silence.

---

## 2. Activation / retention / monetization weaknesses

- **Monetization is half-wired and contradicts the docs (impact: HIGH).** Docs/CONTEXT say payments aren't connected and the owner grants subscriptions manually via `/grant_sub`. The code, however, already has Click/Payme provider tokens, **Telegram Stars**, **manual card transfer**, `pre_checkout` + `successful_payment` → `extend_subscription`, promo codes, and expiry reminders (`sub_expiry_7d/1d/0d`). So the rails exist but the live path is **manual** (card transfer requires an admin to eyeball the bank and tap "confirm"; `sub_card_confirm_*`). That doesn't scale past a few dozen families and injects a human latency (the copy promises "usually under 15 min") into the revenue moment.
  - **Improvement:** Flip on Click/Payme as the default path (the integration is already there) and demote card-transfer to a fallback. Manual confirmation is the #1 thing blocking growth-without-the-owner-in-the-loop.

- **The core value is given away for free, inconsistent with the paywall (impact: HIGH).** `get_active_spreadsheets_with_subscription` polls every family where `subscription_end IS NULL OR > now()`. Self-serve families start with **NULL → monitored (notifications) forever for free**, while AI is gated (`is_subscription_active` returns False on NULL). So the headline feature parents actually want — instant grade alerts — never gets paywalled for self-serve users, even though `sub_features` copy says "without a subscription the bot writes nothing." You're paywalling the AI add-on, not the core.
  - **Improvement:** Decide deliberately. Either (a) embrace it as freemium — notifications free, AI/dashboard/analytics paid (then fix the docs/copy that say otherwise), or (b) set `subscription_end` to a trial window on family creation so monitoring actually expires. Right now it's an accidental policy, and accidental free is the worst kind.

- **No real trial, and "3 tiers" is a misnomer (impact: MED).** There is no time-boxed trial in code (only the one free AI analysis on add-child + a "try AI free" button). And the "3 tiers" are just **3 durations of one identical product** (1/3/12 months) — there is no Free/Plus/Pro feature ladder. That removes the classic upsell lever.
  - **Improvement:** Add an explicit, visible 14-day trial (`subscription_end = now + 14d` on first child added) so the value is felt before the ask, and the expiry reminders you already built have something to fire against. Consider a genuine feature tier (e.g. Free = alerts for 1 child, Paid = AI + dashboard + multi-child) only if you keep B2C.

- **Summer churn is partly handled — credit where due (impact: MED, mitigated).** When grades stop (June–Aug) the product has nothing to notify about, the classic seasonal-churn trap. The team anticipated this: `summer_mode` sends weekly holiday AI "activity ideas" keyed off a holidays calendar, and `weekly_digest_no_grades` says "probably vacation." Good. But billing is still month-based, so a parent paying through summer gets near-zero alert value and is a prime cancel.
  - **Improvement:** Offer a "pause over summer" (freeze `subscription_end`) or default annual plans to bridge the gap; position the annual (-30%) plan as "covers the whole year incl. summer reports."

- **No measured activation/retention loop (impact: MED).** `/status` shows raw counts (families/parents/students) but there's no funnel: invited→authorized, child-add attempt→success, trial→paid, monthly active.
  - **Improvement:** Add a few admin KPIs (activation rate, sheet-connect success rate, paid conversion, summer retention). You can't manage churn you can't see.

---

## 3. Value-prop gaps vs what parents actually need

- **It reports, it doesn't help the parent act (impact: MED–HIGH).** The strongest parent job-to-be-done is "tell me when something needs my attention and what to do." The AI analysis and proactive alerts move toward this, but the default notification is still a raw "new grade: 5." Parents drown in 5s and miss the 2.
  - **Improvement:** Make "attention-worthy" the default surface: lead with downward trends, missed/absent marks, and quarter-grade risk; let the everyday 5s roll up into the evening digest. (You already compute weakest-subject and streaks — route that signal into prioritization.)

- **Single source = single point of failure for trust (impact: MED).** The whole value depends on a teacher diligently maintaining a Google Sheet. If the teacher is late or sloppy, the bot looks broken to the parent. `alert_sheet_stuck` covers the admin side, but the parent has no visibility into data freshness.
  - **Improvement:** Show "last updated by school: <time>" in the dashboard/grades view so a stale sheet reads as "school hasn't posted," not "bot is broken."

- **Multi-child / multi-family comparison is a real differentiator and underexposed (impact: LOW–MED).** `ai_chat_welcome_family` ("compare children") is a genuinely nice hook buried in chat.
  - **Improvement:** Surface family-level comparison and per-child weekly digest as a first-class dashboard tab.

---

## 4. B2C-family vs B2B-school tension

The docs commit to **B2C family** (billing unit = family, `subscription_end` on `families`, parent supplies the sheet). But `CONTEXT.md` openly drifts toward B2B: Tier-3 roadmap names a `schools` table, RBAC (admin→teacher→parent), teacher class-overview, white-label, "RAG for per-school knowledge," and proactive alerts are explicitly called *"a B2B killer feature for the school pitch,"* with milestones phrased as *"after the school contract (Aug–Sep 2026)."*

- **Why this is a real tension, not just naming (impact: HIGH).** Every activation leak in §1 stems from the B2C assumption that *parents* integrate the data source. They can't — the *school/teacher* controls the gradebook. So the natural acquisition motion and the natural integration point are both at the school, while billing and onboarding are built for individual families. The product is architecturally B2C but go-to-market is inevitably B2B.
  - **Improvement — pick the wedge deliberately:**
    - If **B2B-school** is the real plan (the evidence says it is): make the school the unit of onboarding — one teacher connects the class sheet once, parents join by invite (the invite infra already exists and is the cleanest path in the product). Billing can still be per-family underneath, but the *contract* and the *integration* live at the school. Prioritize the teacher view + multi-tenancy that CONTEXT defers.
    - If **B2C** is the real plan: you must remove the teacher dependency (kundalik/Classroom import, or a parent-friendly "ask your teacher to share" flow), otherwise self-serve will keep stalling at the sheet step.
  - Either way, **stop straddling**: the manual `/grant_sub` + manual card confirm is fine for a pilot school but is the wrong machine for B2C scale, and the per-family freemium leak (§2) is the wrong economics for a B2B contract. Align billing, onboarding, and integration to one motion.

---

## 5. Notification UX

What's there (genuinely good): instant vs `summary_only` modes, fixed quiet hours 22:00–07:00 Tashkent with a morning flush, 19:00 evening digest, two-phase confirmation (~5 min) to suppress teacher typos, group-chat delivery with its own quiet-hours queue, streaks, batch titles, quarter-grade and grade-changed events, expiry reminders, and proactive anomaly alerts with 48h dedup.

Gaps:

- **Quiet hours are hard-coded, not user-configurable (impact: MED).** 22:00–07:00 is global. A night-shift parent or one in a different effective routine can't adjust.
  - **Improvement:** Make quiet-hours start/end a per-user setting (you already have a notifications settings screen — `up_notifications`).

- **Opt-out is binary and account-wide (impact: MED).** Only "instant" vs "summary only." No per-child muting, no per-event-type control (e.g. "alert me on 2/3 and absences, digest the rest"), no full pause.
  - **Improvement:** Add per-child mute and an event-type filter; this directly reduces the "drowning in 5s" problem in §3 and lowers notification fatigue → churn.

- **Proactive/attention alerts aren't the headline (impact: MED).** The most valuable, lowest-volume signal (anomaly alerts, weakest subject, quarter risk) is treated the same as routine grades.
  - **Improvement:** Promote attention-alerts to always-on (even in summary mode) and visually distinct, while routine grades respect the digest preference.

- **No delivery/seen feedback loop to tune frequency (impact: LOW).** There's a "Seen" button (`grade_seen_`) but no aggregate read-rate to inform whether instant-mode is overwhelming users.
  - **Improvement:** Track seen-rate by mode; if instant-mode users read <X%, nudge them to summary.

---

## Top 5 priorities (PM call)

1. **Fix the activation cliff** — teacher-side sheet-connect flow / e-diary importer + funnel instrumentation (HIGH). This is the whole ballgame.
2. **Decide B2C vs B2B and align billing+onboarding+integration to it** (HIGH) — the evidence points to B2B-school; stop straddling.
3. **Resolve the freemium leak + turn on automated Click/Payme** (HIGH) — core value is currently free and revenue is manual.
4. **Add a real 14-day trial** so the expiry-reminder + value-first machinery you already built has something to convert (MED).
5. **Make notifications attention-first + give per-child/event opt-out and configurable quiet hours** (MED) — reduces fatigue-driven churn.

Files most relevant to follow-ups (absolute paths): `/Users/nurbekgb/Downloads/IT projects/GradeSentinel_bot/src/handlers/family.py` (sheet-connect flow), `/Users/nurbekgb/Downloads/IT projects/GradeSentinel_bot/src/db/families.py` (line 51 — the NULL=free monitoring gate), `/Users/nurbekgb/Downloads/IT projects/GradeSentinel_bot/src/db/payments.py` (subscription/extend), `/Users/nurbekgb/Downloads/IT projects/GradeSentinel_bot/src/handlers/subscription.py` (manual card-confirm + provider tokens), `/Users/nurbekgb/Downloads/IT projects/GradeSentinel_bot/src/schedulers.py` (summer_mode, quiet-hours, alerts), `/Users/nurbekgb/Downloads/IT projects/GradeSentinel_bot/Docs/CONTEXT.md` (the B2B drift evidence, Tier-3 roadmap).

---

## Часть 3 — Мировая практика: edtech / parent-engagement / нотификации

I have enough material to synthesize. Here is the benchmarked report.

---

# GradeSentinel — World-Practice Benchmark Report
**Parent–school grade notification + AI assistant, Telegram-first, Uzbekistan**
*Research date: 2026-06-29*

## How to read this
Five themes. For each: what the category leaders actually do, then a **specific, opinionated recommendation for GradeSentinel**. GradeSentinel is unusual in three ways that shape every recommendation: (1) it is **parent-first, not teacher-first**; (2) it lives **inside Telegram**, not a standalone app store app; (3) it serves **Uzbekistan**, where Telegram is dominant, multilingual (Uzbek/Russian), and price-sensitive.

---

## 1. Parent–school communication & grade apps: engagement model & retention

**What the leaders do**

- **ClassDojo** — teacher-first, viral parent invites. The teacher adopts it, then *pulls* parents in. Retention comes from a decade of behavior/engagement data refining the feed, plus "moments" (photos, points) that give parents an emotional reason to open daily. Bottom-up adoption bypasses district procurement → low CAC. ([businessmodelcanvastemplate](https://businessmodelcanvastemplate.com/blogs/competitors/classdojo-competitive-landscape), [Common Sense](https://www.commonsense.org/education/best-in-class/the-best-family-communication-platforms-for-teachers-and-schools))
- **Remind** — 30M+ users, SMS-centric. Key insight: **parents don't have to create an account** — messages arrive as plain texts. Lowest possible friction. ([SchoolStatus comparison](https://www.schoolstatus.com/blog/family-teacher-communication-app-comparison))
- **Seesaw** — a digital portfolio: the *child's own work* is the content. Parents respond to their kid's artifacts. The emotional hook (your child's voice/work) drives opens. ([Common Sense](https://www.commonsense.org/education/best-in-class/the-best-family-communication-platforms-for-teachers-and-schools))
- **TalkingPoints** — two-way auto-translation (150 languages), explicitly built for multilingual/low-income families. ESSA Tier-2 evidence: higher test scores, lower absenteeism; 90% of families felt better connected, 85% had more school conversations at home. ([TalkingPoints](https://talkingpts.org/blog/we-used-ai-to-analyze-40-million-family-school-messages-heres-what-we-found/11445/), [Overdeck Foundation](https://overdeck.org/research-repository/family-engagement/engaging-families-leads-to-student-academic-gains-and-increased-attendance-how-talkingpoints-improved-outcomes-in-a-large-urban-school-district/))
- **Bloomz / PowerSchool portals** — Bloomz syncs from the SIS (PowerSchool) to auto-create a parent account per guardian, and translates UI + messages into 250 languages. The SIS is the source of truth; the app is the delivery layer. ([Bloomz](https://www.bloomz.com/), [G2](https://www.g2.com/products/parentsquare/competitors/alternatives))

**The retention pattern across all of them:** the content that earns a daily open is *emotionally about my specific child* (a grade, a behavior point, their artwork), delivered with near-zero friction, in the parent's language.

**Recommendation for GradeSentinel**
- **Lead with the one thing parents check obsessively: their child's grades.** That's your Seesaw-style emotional hook — make the *new-grade event* the core daily-open driver, not generic school announcements.
- **Steal Remind's zero-friction principle, which Telegram makes free:** no app install, no password — a parent taps a deep link and they're in. This is GradeSentinel's single biggest structural advantage; protect it ruthlessly (don't add an account wall before value).
- **Be SIS/journal-driven, not teacher-driven.** Unlike ClassDojo you can't rely on teacher virality in Uzbek schools. Pull grades automatically from the electronic journal (e.g. Kundalik/maktab.uz-class systems) so value flows with zero teacher effort. The teacher should be optional, not a dependency.
- **Bilingual from day one (Uzbek + Russian)** at both UI and message level — this is table-stakes in your market and is precisely what TalkingPoints/Bloomz prove drives the underserved-family engagement and outcomes.

---

## 2. Notification UX & parent-engagement best practices

**What the leaders do**

- **Digest by default.** ParentSquare's default is **Digest**: instant for direct messages + time-sensitive alerts, but everything non-urgent batched into one end-of-day notification. ([Tri-Valley CSD](https://www.trivalleycsd.org/staff-and-student-resources/parentsquare-notification-system/), [West Genesee](https://www.westgenesee.org/article/1701914))
- **Tiered urgency.** The core failure mode named repeatedly: "when a bake-sale alert carries the same weight as a school-closure alert, everything is urgent and nothing is." Leaders separate urgent push from routine digest. ([ParentPortal](https://parentportal.com/blog/view/20251231/push-notifications-that-parents-actually-want-to-receive/))
- **Per-category preferences.** Best platforms let a parent choose instant push for a new message but daily digest for homework. ([SchoolStatus planning guide](https://www.schoolstatus.com/blog/school-communications-planning-guide))
- **Easy opt-out + re-engagement.** STOP-to-opt-out (ParentSquare), and automated "friendly check-in" nudges to non-engaging parents rather than more noise. ([CallHub](https://callhub.io/blog/education/parent-notification-system/), [SchoolCues](https://www.schoolcues.com/blog/step-by-step-guide-to-automating-parent-notifications-via-text-alerts-in-montessori-schools/))

**Recommendation for GradeSentinel**
- **Three notification tiers, hard-coded into the product:**
  1. **Instant** — a new grade (especially a failing/low grade), absence, or a direct teacher message. This is your "open the app" event.
  2. **Daily digest** — a single end-of-day summary: "Today: 2 grades, 1 homework, attendance OK." This should be the **default** for routine items (follow ParentSquare).
  3. **Weekly summary** — trend view: GPA movement, attendance %, subjects trending down.
- **Quiet hours by default** (e.g. no non-urgent pushes 21:00–08:00). A grades app that pings at 11pm gets muted/blocked — and a muted Telegram bot is dead.
- **Per-child + per-category toggles** since a parent may have 2–3 kids; let them tune each.
- **Re-engagement as value, not nag:** if a parent hasn't opened in 7 days, send *one* high-signal message ("Aziz's math grade dropped this week — see why"), never "we miss you." Cap re-engagement frequency.
- **Frictionless mute/pause** (e.g. "pause for exam week"). Respecting opt-out keeps you out of Telegram's spam filters and preserves long-term trust.

---

## 3. AI in edtech for parents (nudges/coaching, tutoring assistants)

**What the research & leaders show**

- **Tutor-mode helps, assistant-mode can hurt.** Across three RCTs, AI in *tutor* mode (Socratic, scaffolded) beat in-class active learning by 0.73–1.3 SD, while answer-giving "assistant" mode hurt learning. A UK RCT (ages 13–15) found supervised AI tutors outperformed online human tutors on later problem-solving. ([aitoolsforkids summary of Harvard/Scientific Reports](https://www.aitoolsforkids.com/blog/ai-tutor-vs-assistant-for-kids), [BEA 2025 shared task](https://arxiv.org/pdf/2507.10579))
- **Age matters.** AI tutoring works for upper-primary/middle/secondary; young children can't prompt well. ([thirdspacelearning](https://thirdspacelearning.com/us/blog/ai-in-education/))
- **AI-as-coach-for-the-adult works well and is lower-risk.** TalkingPoints' **Message Mentor** doesn't talk to the child — it coaches the *teacher's* draft toward positive, culturally responsive, actionable wording, with the human always able to edit/reject. This human-in-the-loop framing is the safest, highest-trust AI pattern in the category. ([techaimag](https://www.techaimag.com/ai-companies/talkingpoints), [TalkingPoints](https://talkingpts.org/blog/we-used-ai-to-analyze-40-million-family-school-messages-heres-what-we-found/11445/))
- **Pitfalls:** hallucinated/over-helpful answers, replacing rather than scaffolding, and weak fit for very young learners.

**Recommendation for GradeSentinel**
- **Position AI as the parent's coach, not the child's tutor.** This is your TalkingPoints-style sweet spot and dodges the K-12 child-safety/hallucination minefield. Concretely:
  - **Explain the grade in plain language:** "A '3' in algebra this week is below Aziz's usual '4'. The topic was quadratic equations."
  - **Give the parent an action script:** 2–3 concrete, supportive things to say/do tonight ("ask him to re-do problems 4–6", "praise effort, not just score"). This is the nudge that research shows moves attendance/achievement.
  - **Trend interpretation:** "Math has slipped 3 weeks running — consider talking to the teacher" with a one-tap "draft a polite message to the teacher" (Message-Mentor pattern, human edits before send).
- **Keep AI human-in-the-loop and grounded.** Generate only from the child's real grade/attendance data you hold; never let it invent facts. Always show the underlying data next to the AI text.
- **Optional, age-gated homework help.** If you offer a child-facing tutor later, use **tutor mode (Socratic, no direct answers)**, target middle/secondary only, and make it clearly a separate, opt-in surface. Don't bolt answer-giving onto the parent flow.
- **AI is your premium wedge** (see §5) — coaching/explanations are exactly the recurring value parents will pay for, while raw grade notifications stay free.

---

## 4. Telegram-bot SaaS product & monetization patterns

**What the ecosystem does**

- **Telegram Stars** is the native digital-goods currency; bots can create **Star subscriptions** that recur, but Stars are bought via Apple/Google IAP — meaning **Apple/Google take ~30%**, and Stars recurring billing is still limited/immature for true SaaS. ([Telegram payments-stars](https://core.telegram.org/bots/payments-stars), [Star subscriptions](https://core.telegram.org/api/subscriptions), [GramBase 2026 guide](https://grambase.ai/blog/telegram-stars-guide-2026))
- **For real recurring revenue, move the paywall off Telegram.** Telegram's own Bot Payments API supports 20+ providers across 200+ countries for card payments; the consistent advice for SaaS is: **keep Telegram for chat/distribution, host the paywall + billing on your own site/provider** for stable recurring revenue, flexible pricing, and analytics. ([Telegram Bot Payments](https://core.telegram.org/bots/payments), [OmiSoft Mini-App monetization](https://omisoft.net/gb/blog/how-to-monetize-telegram-mini-app/))
- **Hybrid models win.** Best Mini Apps combine ≥2 models — e.g. free/ad tier for casual users + IAP/subscription for engaged ones. ([OmiSoft](https://omisoft.net/gb/blog/how-to-monetize-telegram-mini-app/))
- **"Rule of 90":** ~90% of revenue is decided in the first session — onboarding and the first paywall are the highest-leverage surfaces. ([OmiSoft](https://omisoft.net/gb/blog/how-to-monetize-telegram-mini-app/))

**Recommendation for GradeSentinel**
- **Two payment rails, by region:**
  - **Local cards (primary):** integrate Uzbek payment providers (Payme, Click, Uzum) via a **Telegram Mini App / external checkout** for recurring subscriptions — avoids the 30% Apple/Google cut and matches how Uzbeks actually pay. This is your main monetization rail.
  - **Telegram Stars (secondary):** offer as a frictionless one-tap option for tips/one-off unlocks or users who prefer it, accepting the fee.
- **Build the bot as the distribution + notification layer, and a lightweight Mini App for the paywall, settings, and dashboards** (grade history, trends). This matches the "Telegram for chat, your surface for billing" consensus and gives you the analytics you'll need.
- **Treat first-session as sacred (Rule of 90):** the parent's first interaction must end with them having seen a *real grade for their real child* and one AI explanation — before any payment ask.
- **Hybrid tiering:** free notification tier (broad reach, viral parent-to-parent sharing) + paid AI/insights tier. Don't gate the core grade alert; gate the intelligence on top of it.

---

## 5. Free-trial / freemium / activation for parent apps

**What the benchmarks say**

- **Conversion benchmarks:** opt-in free trial 8–25%, opt-out trial 50–75%, **pure freemium just 2–5%**. ([Appcues](https://www.appcues.com/blog/free-to-paid-conversion-strategies), [Userpilot](https://userpilot.com/blog/saas-average-conversion-rate/))
- **Activation = fast "aha".** Users who hit their aha moment in the first minutes convert far more; the three root causes of low conversion are misaligned value, excess friction, and poor upgrade timing. ([Amplitude](https://amplitude.com/blog/increasing-free-trial-conversion), [Userpilot](https://userpilot.com/blog/increase-trial-to-paid-conversion-rate/))
- **Personalized onboarding** that routes different users to different value paths, plus a welcome/onboarding message series, drives activation. ([CXL](https://cxl.com/blog/freemium-conversions/), [Shopify](https://www.shopify.com/partners/blog/app-onboarding))

**Recommendation for GradeSentinel**
- **Freemium core + opt-out trial on premium.** Keep grade notifications permanently free (reach, virality, the moral case in your market). For the AI/insights tier, prefer an **opt-out-style trial** ("14 days of AI coaching included, cancel anytime") given the dramatically higher conversion vs. pure freemium — but make cancellation genuinely one-tap to stay trustworthy and Telegram-spam-safe.
- **Engineer the aha in the first 60 seconds:** onboarding = pick your child → instantly show *their real latest grade* + one AI explanation of what it means and what to do. That single moment is your activation event; instrument it.
- **Personalize the path** by child's grade level and by parent goal ("stay informed" vs "help improve grades") — route the latter straight into the AI value.
- **Time the upgrade ask at a high-emotion moment**, not on a timer: when a grade drops or a negative trend appears, that's when the parent most wants the coaching — surface the paywall there ("See why this dropped + what to do → unlock AI insights").
- **Onboarding message series in the bot:** 3–4 well-spaced messages over the first week demonstrating one premium capability each, ending in the trial-to-paid prompt.

---

## Top 7 actions for GradeSentinel (synthesis)
1. **Make the new-grade event the daily-open hook** — Seesaw's emotional pull, delivered with Remind-style zero friction (no install/password — Telegram's superpower).
2. **Be journal/SIS-driven, not teacher-driven** — auto-pull grades so value needs no teacher effort.
3. **Three notification tiers + default quiet hours + per-child/per-category toggles** — a grades bot that over-pings gets muted, and a muted bot is dead.
4. **AI = coach for the parent (explain grade + action script), human-in-the-loop, grounded in real data** — TalkingPoints Message-Mentor pattern; avoid child-facing answer-giving.
5. **Bilingual Uzbek/Russian at UI + message level** — proven engagement/outcome driver for multilingual families.
6. **Monetize via local cards (Payme/Click/Uzum) in a Mini App for recurring billing; Stars as one-tap secondary** — avoid the 30% app-store cut; keep the bot as distribution.
7. **Free grade alerts + opt-out trial on AI tier, aha in 60 s, upgrade ask at the grade-drop moment** — freemium reach with trial-grade conversion.

## Sources
- [ClassDojo competitive landscape](https://businessmodelcanvastemplate.com/blogs/competitors/classdojo-competitive-landscape) · [Common Sense — best family communication platforms](https://www.commonsense.org/education/best-in-class/the-best-family-communication-platforms-for-teachers-and-schools) · [SchoolStatus app comparison](https://www.schoolstatus.com/blog/family-teacher-communication-app-comparison)
- [ParentSquare notifications (Tri-Valley)](https://www.trivalleycsd.org/staff-and-student-resources/parentsquare-notification-system/) · [West Genesee notifications](https://www.westgenesee.org/article/1701914) · [Push notifications parents want](https://parentportal.com/blog/view/20251231/push-notifications-that-parents-actually-want-to-receive/) · [SchoolStatus comms planning](https://www.schoolstatus.com/blog/school-communications-planning-guide) · [CallHub parent notification systems](https://callhub.io/blog/education/parent-notification-system/)
- [AI tutor vs assistant (RCT summary)](https://www.aitoolsforkids.com/blog/ai-tutor-vs-assistant-for-kids) · [BEA 2025 tutor evaluation](https://arxiv.org/pdf/2507.10579) · [AI in US schools 2026](https://thirdspacelearning.com/us/blog/ai-in-education/) · [TalkingPoints Message Mentor / 40M messages](https://talkingpts.org/blog/we-used-ai-to-analyze-40-million-family-school-messages-heres-what-we-found/11445/) · [Overdeck — TalkingPoints outcomes](https://overdeck.org/research-repository/family-engagement/engaging-families-leads-to-student-academic-gains-and-increased-attendance-how-talkingpoints-improved-outcomes-in-a-large-urban-school-district/) · [techaimag — TalkingPoints AI](https://www.techaimag.com/ai-companies/talkingpoints)
- [Telegram Bot Payments (Stars)](https://core.telegram.org/bots/payments-stars) · [Star subscriptions API](https://core.telegram.org/api/subscriptions) · [Telegram Bot Payments (cards/providers)](https://core.telegram.org/bots/payments) · [GramBase Stars guide 2026](https://grambase.ai/blog/telegram-stars-guide-2026) · [OmiSoft — Mini App monetization](https://omisoft.net/gb/blog/how-to-monetize-telegram-mini-app/)
- [Appcues free-to-paid](https://www.appcues.com/blog/free-to-paid-conversion-strategies) · [Userpilot conversion benchmarks](https://userpilot.com/blog/saas-average-conversion-rate/) · [Amplitude trial conversion](https://amplitude.com/blog/increasing-free-trial-conversion) · [CXL freemium onboarding](https://cxl.com/blog/freemium-conversions/) · [Shopify app onboarding](https://www.shopify.com/partners/blog/app-onboarding)
- [Bloomz](https://www.bloomz.com/) · [G2 ParentSquare alternatives](https://www.g2.com/products/parentsquare/competitors/alternatives)

---

## Часть 4 — Мировая практика: архитектура данных + school-B2B GTM

# GradeSentinel — World-Practice Research: Architecture & Go-To-Market

Telegram grade-notification bot, currently scraping teacher-maintained Google Sheets gradebooks, exploring a school-contract B2B direction. Each theme below gives the industry standard (benchmarked) and a specific recommendation for GradeSentinel.

---

## 1. Data source reliability — spreadsheet scraping vs. proper integration

**Industry standard.** Mature edtech does not scrape spreadsheets; it ingests via standardized rostering/grade APIs. The two dominant standards in K-12:
- **1EdTech OneRoster 1.2** — the vendor-facing standard for rostering and gradebook exchange. Three independently-implementable services (Rostering, Gradebook, Resource), available in both **REST API** and **CSV/SFTP** modes. This is the standard already spoken by SIS platforms (PowerSchool, Infinite Campus, Skyward) and rostering brokers (Clever, ClassLink, Edlink).
- **Ed-Fi Data Standard** — a broader district-wide data model / Operational Data Store (ODS) that becomes the "single source of truth" across SIS, assessment, behavior, HR. Ed-Fi and 1EdTech now ship a joint Ed-Fi→OneRoster service, so the two are converging rather than competing.

Ingestion maturity ladder, worst→best: (1) screen/cell scraping → (2) nightly CSV drop to SFTP → (3) OneRoster REST with OAuth2 scoped read → (4) full SIS/Ed-Fi ODS integration. REST gives real-time pull, **field-level scoping** (grant read-only to grades, nothing else), and content validation; SFTP/CSV is batch-only, flat, and "SSH secures transport but cannot verify the data content" — errors propagate silently.

**Risk of depending on teacher-maintained Google Sheets.** This is the weakest rung of the ladder and carries compounding risks:
- **Structural fragility** — there is no schema contract. A teacher renaming a tab, inserting a column, merging cells, or reformatting a date silently breaks the scraper. You are coupled to human formatting habits, not a versioned API.
- **Quota/availability ceiling** — Google Sheets API caps at ~300 read req/min per project and 60/min per user; over-limit returns `429`, requiring exponential backoff, and Google has announced **billing for excess quota in 2026**. At scale across many schools this becomes both a cost and a hard scaling wall.
- **No authority/consent chain** — scraping a teacher's personal sheet bypasses the school as the data controller. Under a B2B contract the *school* must authorize the data flow; a personal-sheet dependency has no legal footing (see §4).
- **Data quality** — no validation, no referential integrity (a student row may not map to a real enrollment), no audit of who changed what.

**Recommendation for GradeSentinel.** Treat Google Sheets scraping as a *bootstrap/MVP* mechanism only, and architect a **pluggable ingestion adapter layer** now so the data source is swappable behind a stable internal interface. For the B2B pivot, define an internal canonical model aligned to **OneRoster 1.2 Gradebook + Rostering** vocabulary (orgs, users, classes, enrollments, line-items, results). Then offer schools, in order of effort: (a) a **structured GradeSentinel-owned spreadsheet template** with locked headers + validation as the lowest-friction onboarding (turns chaotic scraping into a contract'd schema), and (b) a **OneRoster CSV/REST connector** for any school whose SIS supports it. This makes "we speak the K-12 interoperability standard" a sales asset and removes the existential single-point-of-failure of brittle scraping. Where Uzbek schools run a national e-gradebook platform, prioritize a **direct API/data-sharing agreement** with that platform over per-teacher sheet scraping.

---

## 2. Change detection — polling vs. webhooks/event-driven at scale

**Industry standard.** Webhooks/event-driven beat polling on latency (ms vs. minutes) and cost (you pay per *event*, not per *poll*); polling cost scales with frequency regardless of whether data changed, and a high volume of polls saturates CPU/memory and "becomes distributed state management across many systems" that doesn't scale cleanly across many tenants. **But** most upstream SaaS data sources (SIS, Google Sheets) **do not emit reliable webhooks** — so the mature pattern is the **"virtual webhook"**: centralize polling+change-detection in one integration layer, diff against last-known state, and emit internal events to the rest of the system. This gets event-driven ergonomics downstream while tolerating dumb upstreams.

**Recommendation for GradeSentinel.** Google Sheets has no usable per-cell push, so pure event-driven from the source is impossible — use the **virtual-webhook pattern**: one tenant-aware ingestion worker polls each source on an **adaptive schedule** (frequent during school hours, sparse overnight/weekends/holidays), computes a content hash/diff against the last snapshot, and on a real delta emits an internal `grade.changed` event onto a queue. Notification fan-out (Telegram messages to parents) subscribes to that event, fully decoupled from polling. Respect Sheets quotas with **exponential backoff + batched range reads + per-project quota budgeting** so one large district can't starve others. When you reach OneRoster REST or an SIS with delta endpoints, swap the poller for incremental sync behind the same internal event interface — downstream code never changes.

---

## 3. Multi-tenant SaaS architecture for school-B2B

**Industry standard.** AWS canon: three tenancy models — **silo** (DB/stack per tenant), **pool** (shared, tenant_id column), **bridge** (mix). The pragmatic default for a small team is **bridge**: start pooled, silo the DB/background jobs for premium or compliance-sensitive tenants later. The cardinal rule: **every request must resolve to exactly one tenant, and every authorization decision is scoped to that tenant** — the tenant picker is a *hint*, never authoritative. RBAC: roles carry permission sets scoped within a tenant.

**Documented pitfalls (the ones that cause breaches):** a missing tenant predicate in a query; a JWT whose tenant claim is never checked against the resource being accessed; a just-in-time user-provisioning flow that creates a user without binding them to the right tenant; trusting a client-supplied tenant selector. A shared-DB + `tenant_id` column gives logical separation but is "insufficient" for strict compliance — one app bug or leaked credential exposes *all* tenants at once.

**Recommendation for GradeSentinel.** Model tenancy as **School (tenant) → roles: School Admin → Teacher → Parent/Student**, with a **pooled DB + mandatory `school_id` scoping enforced at the data layer** (e.g., a default queryset/row-level-security filter, not ad-hoc per-query `WHERE` — this is exactly the "missing tenant predicate" pitfall). Bind every Telegram identity to a `(school_id, role)` at link-time; never infer tenant from a user-supplied value. RBAC capabilities: School Admin manages roster/config/branding; Teacher sees only their classes; Parent sees only their child(ren)'s grades. Given student-data sensitivity and Uzbek localization (§4), plan a **silo escape hatch**: keep the schema silo-ready so a large district or a government contract can be moved to a dedicated DB without re-architecture. For per-school config/white-label, keep a `SchoolSettings` table (branding, notification rules, grade-scale, language RU/UZ, which subjects are tracked) rather than code branches — config-as-data, mirroring RailTech's own `CompanySettings`/`DocumentProfile` tenant-hub pattern you already built.

---

## 4. K-12 B2B go-to-market, procurement, pricing, compliance

**Industry standard — sales motion.** K-12 procurement is slow and committee-driven: most purchases take **6+ months** from need to signature, and **add ~a year** if a pilot is required (most student/teacher-facing tools require one). Decisions need consensus across teachers, IT director, curriculum, and purchasing — evaluation committees of **5-7 people**. Budget cycle (US benchmark): **July 1 fiscal year**, budgets discussed Mar-Apr, so you warm decision-makers by January and deliver proposals by March. **Pilots are the wedge** — land a free/cheap pilot in 1-3 schools, prove value, expand to district.

**Pricing.** K-12 is highly sensitive to **per-student-per-month (PS/PM)** pricing; alternatives are **flat campus/site license**, **per-teacher**, and **freemium-to-upsell**. Per-student aligns price to value but can sticker-shock large schools — site/district licenses smooth that.

**Compliance.** US baseline = **FERPA** (governs education records; binds vendors as a "school official" under contract via a Data Processing Agreement) + **COPPA** (under-13 data; revised opt-in consent model effective Apr 2026) + state laws (SOPIPA/SOPPA/NY Ed-Law-2-d). Key nuance: **SOC 2 Type II validates security but does NOT by itself satisfy FERPA/COPPA**. Core contractual asks vendors must meet: disclose what's collected & why, data minimization, retention limits, deletion on request, breach notification, and **no advertising/secondary use of student data**.

**Data localization (Uzbekistan).** Critical and binding: since **16 April 2021**, personal data of Uzbek citizens must be **processed and stored on technical means physically located in Uzbekistan**, in a **database registered with the State Personalization Center / UzComNazorat** (registration takes ~15 days). Applies to local *and* foreign operators; the **database owner/operator** carries the obligation. Non-compliance can lead to **administrative/criminal penalties and blocking of the online resource** in Uzbekistan. (Note: 2026 amendments are moving toward a more flexible model — e.g. enabling Apple/Google Pay — so monitor, but assume localization applies to children's grade data.)

**Recommendation for GradeSentinel.**
- **Motion:** Sell to the **school as the tenant/data controller**, not to individual teachers. Lead with a **free single-school pilot** timed to the Uzbek school calendar (target the term boundary / new academic year for budget and onboarding). Build the case study, then expand school→district and pursue regional education-department endorsement as the multiplier.
- **Pricing:** Default **per-student-per-month**, but offer a **flat per-school annual license** as the headline B2B SKU (predictable for school budgets, avoids per-head sticker shock and simplifies procurement). Keep the existing consumer/parent freemium as a bottom-up demand-generation funnel into B2B.
- **Compliance (non-negotiable for the pivot):** Host all student/parent personal data **inside Uzbekistan** on in-country infrastructure and **register the database with UzComNazorat before processing** — this is a launch gate, not a backlog item, and it doubles as a competitive moat against foreign edtech that can't easily localize. Put a **school data-processing agreement** in your standard contract (purpose limitation, no secondary/ad use of grades, retention + deletion, breach notification, role-scoped access). Use FERPA/COPPA principles as your *design baseline* even though they're US law — they're what international districts and privacy-conscious parents expect.

---

## 5. Auth without SMS (OTP-via-bot / magic links)

**Industry standard.** Passwordless trade-offs: **magic links** = one-click, best for infrequent desktop logins, but require email and a working inbox round-trip; **OTP** = code entry, better for mobile-first / in-app flows where the user shouldn't leave the app. Best practice for either: **unique hard-to-guess tokens, short expiry, single-use, rate-limiting, and login notifications** so a user knows if a link/code was used. In SIM-swap-prone regions, **avoid SMS** and prefer email/in-app/authenticator channels — directly relevant to skipping SMS in Uzbekistan. Best architecture offers a **primary method + fallback**.

**Telegram-native auth** is the natural fit and the strongest channel you already own:
- **Telegram Login Widget** — server verifies an HMAC-SHA-256 of the sorted `data-check-string` keyed by **SHA256(bot_token)**, and checks `auth_date` freshness to block replay. Client side needs only the numeric **bot ID — never expose the full bot token client-side**.
- Telegram now also supports **standard OIDC** (Authorization Code + PKCE; validate the `id_token` JWT against Telegram's JWKS, check `iss=https://oauth.telegram.org`, `aud=bot ID`, `exp`) — so you can plug into any OIDC library/IdP.
- Register allowed redirect URLs in **@BotFather**; webhooks must be HTTPS/TLS 1.2+.

**Recommendation for GradeSentinel.** Since every user is already in Telegram, make **Telegram identity the primary auth** — no SMS, no passwords. For account linking use a **single-use, short-TTL, signed deep-link token** (`t.me/GradeSentinelBot?start=<token>`): the School Admin (or teacher) issues a parent an invite token bound to `(school_id, student_id, role)`; the parent taps it, the bot resolves and verifies the token server-side, and the chat is bound to that tenant+role. Tokens: cryptographically random, single-use, expire fast, and the bot confirms the link in-chat (the "notify on login" best practice). For the **web admin console** (School Admin RBAC), use the **Telegram Login Widget or Telegram-OIDC** with proper HMAC/JWT verification and `auth_date` replay protection, with an **email magic link as fallback** for admins who prefer desktop. This keeps you SIM-swap-proof, SMS-cost-free, and consistent with the channel parents already use.

---

## Top priorities (synthesis)

1. **De-risk the data source** — abstract ingestion behind a OneRoster-aligned adapter; demote raw Sheets scraping to MVP-only; offer a locked-schema template + OneRoster connector for B2B.
2. **Localize + register data in Uzbekistan before B2B launch** — hard legal gate and a moat.
3. **Tenant-isolation discipline** — enforce `school_id` scoping at the data layer (RLS/default filter), bind Telegram identities to `(school_id, role)`, keep a silo escape hatch.
4. **Virtual-webhook change detection** — centralized adaptive poller → internal events → decoupled notification fan-out.
5. **Telegram-native passwordless auth** with signed single-use deep-link tokens; HMAC/`auth_date` verification; email magic-link fallback for admins.
6. **GTM:** pilot-led, sell to the school as controller, per-school flat license headline SKU with PS/PM option, DPA in every contract.

---

## Sources

Data source / interoperability standards:
- [1EdTech OneRoster](https://www.1edtech.org/standards/oneroster) · [Integrating OneRoster and Ed-Fi 2025 (PDF)](https://www.1edtech.org/sites/default/files/media/docs/2025/WBR_1EdTech%20OneRoster%20and%20Ed-Fi.pdf) · [Ed-Fi OneRoster docs](https://docs.ed-fi.org/reference/oneroster/) · [Ed-Fi vs OneRoster — Magic EdTech](https://www.magicedtech.com/blogs/ed-fi-vs-oneroster-in-plain-english-when-to-use-each-in-k-12/) · [What is SIS Integration — Panorama](https://www.panoramaed.com/blog/what-is-sis-integration) · [OneRoster 1.2 — Edlink](https://ed.link/community/everything-you-need-to-know-about-oneroster-1-2/) · [Rostering options — Edlink](https://ed.link/community/rostering-classroom-data-in-your-lms-what-are-your-options/) · [SFTP vs API — Merge.dev](https://www.merge.dev/blog/sftp-vs-api-integrations) · [New era of K12 rostering — EdTech Insiders](https://edtechinsiders.substack.com/p/the-new-era-of-k12-rostering-what)
- Google Sheets fragility: [Sheets API usage limits](https://developers.google.com/workspace/sheets/api/limits) · [Apipheny — Sheets API pricing/limits](https://apipheny.io/google-sheets-api/) · [NoCodeAPI — rate limit hacks](https://nocodeapi.com/stop-wasting-time-on-api-rate-limits-5-google-sheets-sync-hacks-that-actually-work/)

Polling vs webhooks:
- [Unified.to — Polling vs Webhooks](https://unified.to/blog/polling_vs_webhooks_when_to_use_one_over_the_other) · [Unified.to — Which delivery model scales](https://unified.to/blog/which_event_delivery_model_scales_polling_webhooks_or_virtual_webhooks) · [Unified.to — Virtual webhooks](https://unified.to/blog/virtual_webhooks_vs_polling_jobs_how_unified_api_platforms_handle_change_detection) · [Edana — Polling vs Webhooks strategy](https://edana.ch/en/2026/04/03/polling-vs-webhooks-how-to-choose-the-right-api-integration-strategy/) · [bugfree.ai — system design tradeoffs](https://bugfree.ai/knowledge-hub/webhook-vs-polling-system-design-tradeoffs)

Multi-tenant architecture & RBAC:
- [AWS — Tenant isolation](https://docs.aws.amazon.com/whitepapers/latest/saas-architecture-fundamentals/tenant-isolation.html) · [SSOJet — Multi-tenant identity](https://ssojet.com/blog/multi-tenant-identity-management) · [WorkOS — multi-tenant guide](https://workos.com/blog/developers-guide-saas-multi-tenant-architecture) · [Clerk — design multitenant SaaS](https://clerk.com/blog/how-to-design-multitenant-saas-architecture) · [RBAC for multi-tenant SaaS — Medium](https://medium.com/@my_journey_to_be_an_architect/building-role-based-access-control-for-a-multi-tenant-saas-startup-26b89d603fdb)

GTM / procurement / pricing / compliance:
- [NationGraph — navigating K-12 procurement](https://www.nationgraph.com/post/education-technology-companies-k12-procurement) · [Monetizely — education SaaS pricing](https://www.getmonetizely.com/articles/the-complete-guide-to-running-a-pricing-and-packaging-strategy-project-for-education-saas) · [RAYSolute — B2B EdTech K-12 sales](https://www.raysolute.com/edtech-b2b-sales-strategy-schools.html) · [Prospeo — EdTech GTM playbook 2026](https://prospeo.io/s/edtech-go-to-market-strategy)
- Privacy: [McDermott — EdTech privacy landscape](https://www.mcdermottlaw.com/insights/edtech-and-privacy-navigating-a-shifting-regulatory-landscape/) · [TheSOC2 — FERPA/COPPA/SOC2 2026](https://www.thesoc2.com/post/edtech-compliance-2026-ferpa-coppa-and-soc2-requirements-explained) · [US Dept of Ed — EdTech vendors](https://studentprivacy.ed.gov/audience/education-technology-vendors)
- Uzbekistan localization: [Library of Congress — Uzbek data localization in force](https://www.loc.gov/item/global-legal-monitor/2021-05-07/uzbekistan-new-requirements-for-uzbek-citizens-personal-data-localization-enter-into-force/) · [Dentons — localization effective Apr 2021](https://www.dentons.com/en/insights/alerts/2021/january/25/uzbekistan-data-localization-requirement-to-be-effective-in-april-2021) · [Dentons — non-compliance blocking](https://www.dentons.com/en/insights/articles/2021/march/2/non-compliance-with-data-localization-may-lead-to-restriction-of-access-to-online-resources) · [Gazeta.uz — 2026 amendments](https://www.gazeta.uz/en/2026/01/21/data/)

Auth without SMS:
- [Scalekit — OTP vs Magic Links](https://www.scalekit.com/blog/otp-vs-magic-links-passwordless-authentication) · [JumpCloud — Magic Links vs OTP](https://jumpcloud.com/blog/magic-links-vs-one-time-passwords-otp-a-technical-comparison) · [Authgear — passwordless comparison](https://www.authgear.com/post/passwordless-authentication-magic-links-passkeys-otp/) · [Telegram Login Widget](https://core.telegram.org/widgets/login) · [Log In With Telegram](https://core.telegram.org/bots/telegram-login) · [BAZU — securing Telegram bots](https://bazucompany.com/blog/how-to-secure-telegram-bots-with-authentication-and-encryption-comprehensive-guide-for-businesses/)
