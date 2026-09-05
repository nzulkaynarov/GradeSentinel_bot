/**
 * GradeSentinel WebApp — Telegram Mini App.
 *
 * Жизненный цикл:
 *   1. Парсим initData из Telegram WebApp SDK
 *   2. Загружаем переводы (locale.json)
 *   3. /api/dashboard/init — список учеников + язык юзера
 *   4. Рендерим переводы по data-i18n
 *   5. /api/dashboard/<student_id>?days=7 — все данные за один запрос
 *   6. Рендерим hero, графики, секции
 *   7. По требованию: /api/quarters/<id> (lazy)
 *
 * Перформанс:
 *   - Skeleton показывается мгновенно при загрузке HTML
 *   - i18n + dashboard /init грузятся параллельно
 *   - Chart.js bundled локально (ноль CDN зависимостей)
 *   - Один API роут вместо трёх
 */

const tg = window.Telegram?.WebApp;
const initData = tg?.initData || "";
const API_HEADERS = { "X-Telegram-Init-Data": initData };

// ============ STATE ============

const state = {
    lang: "ru",
    translations: {},
    students: [],
    currentStudentId: null,
    currentDays: 90,          // = кнопке с классом active в dashboard.html («Четверть»)
    dashboard: null,            // последний загруженный snapshot
    quarters: null,             // lazy-loaded
    quartersLoading: false,
    yearReport: null,           // lazy-loaded, end-of-year отчёт
    yearLoading: false,
    currentView: "today",       // активная вкладка нижней навигации
};

// localStorage ключ для last-seen timestamp (подсветка "новое")
const LAST_SEEN_KEY = (studentId) => `gs_lastseen_${studentId}`;

// ============ INIT ============

if (tg) {
    tg.ready();
    tg.expand();
    _applySafeArea();
    _setupSwipeGuard();
}

// Нижняя навигация закреплена у края экрана и на iPhone попадает под системную
// полосу. Telegram отдаёт отступы в safeAreaInset (Bot API 8.0) — прокидываем
// их в CSS-переменную. Всё за проверкой версии: на старом клиенте поля просто
// нет, и обращение к нему уронило бы инициализацию дашборда целиком.
function _applySafeArea() {
    if (!tg?.isVersionAtLeast?.("8.0")) return;
    const apply = () => {
        const bottom = tg.safeAreaInset?.bottom ?? 0;
        document.documentElement.style.setProperty("--gs-safe-bottom", `${bottom}px`);
    };
    apply();
    // Поворот экрана меняет отступы — Telegram присылает событие.
    try {
        tg.onEvent?.("safeAreaChanged", apply);
    } catch (e) {
        console.warn("safeAreaChanged subscribe failed", e);
    }
}

// Вертикальный свайп по длинному списку оценок сворачивал Mini App вместо
// прокрутки (Bot API 7.7 позволяет это отключить).
function _setupSwipeGuard() {
    if (!tg?.isVersionAtLeast?.("7.7")) return;
    try {
        tg.disableVerticalSwipes?.();
    } catch (e) {
        console.warn("disableVerticalSwipes failed", e);
    }
}

document.addEventListener("DOMContentLoaded", boot);

async function boot() {
    try {
        // Параллельно: lang/students bootstrap + переводы (после того как узнаем язык)
        const initRes = await fetchJSON("/api/dashboard/init");
        state.lang = initRes.user?.lang || "ru";
        state.students = initRes.students || [];
        state.botUsername = initRes.bot_username || null;

        // Документ-уровень атрибут lang для accessibility
        document.documentElement.lang = state.lang;

        // Загружаем переводы для определённого языка
        state.translations = await loadTranslations(state.lang);
        applyTranslations(document);

        // Greeting
        renderGreeting(initRes.user);

        if (state.students.length === 0) {
            return showError(t("error_no_students"));
        }

        // Студенты: tabs если >1, скрытый header если 1
        renderStudentTabs(state.students);
        state.currentStudentId = state.students[0].id;

        // Загрузить dashboard первого ученика
        await loadDashboard();

        // Привязать period buttons
        document.querySelectorAll(".period-btn").forEach(btn => {
            btn.addEventListener("click", () => onPeriodChange(btn));
        });

        // Привязать collapsible секции
        document.querySelectorAll(".collapsible .toggle-btn").forEach(btn => {
            btn.addEventListener("click", () => toggleSection(btn.closest(".collapsible")));
        });

        // Кнопка retry на error экране
        document.getElementById("error-retry").addEventListener("click", () => {
            hide("error");
            show("skeleton");
            boot();
        });

        // Dashboard refresh: action bar (PDF + AI deep-link). Share убран.
        setupActionBar();

        // Drill-down hash router (#subject/<name>) + back button
        window.addEventListener("hashchange", _handleHashChange);
        const ddBack = document.getElementById("drilldown-back");
        if (ddBack) ddBack.addEventListener("click", closeDrilldown);

        // Показать контент, скрыть скелетон
        hide("skeleton");
        show("content");

        // Если открыт URL с hash — сразу показать drill-down
        if (window.location.hash) _handleHashChange();
    } catch (e) {
        console.error("Boot failed", e);
        showError(t("error_generic") + ": " + e.message);
    }
}

// ============ FETCH HELPERS ============

async function fetchJSON(url) {
    const res = await fetch(url, { headers: API_HEADERS });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

async function loadTranslations(lang) {
    try {
        // cache: 'no-cache' — браузер делает revalidate (304 если не изменилось),
        // но НЕ хранит вечно как force-cache. После deploy новые i18n ключи
        // подтягиваются. Стоимость: 1 HEAD-equivalent request per visit.
        // Раньше force-cache → новые ключи рендерились как "kpi_avg" буквально.
        // Версионируем тем же build_id, что и app.js/style.css: статика теперь
        // кэшируется надолго, и без ключа версии перевод залипал бы навсегда.
        const v = window.GS_BUILD_ID || "";
        const res = await fetch(`/static/locales/${lang}.json?v=${encodeURIComponent(v)}`);
        if (!res.ok) throw new Error(`locale ${lang} not found`);
        return await res.json();
    } catch (e) {
        // Fallback на ru если запрошенный язык недоступен
        if (lang !== "ru") {
            console.warn(`Locale ${lang} fallback to ru`, e);
            return loadTranslations("ru");
        }
        throw e;
    }
}

// ============ I18N ============

function t(key, vars = {}) {
    let s = state.translations[key] || key;
    for (const [k, v] of Object.entries(vars)) {
        s = s.replace(`{${k}}`, v);
    }
    return s;
}

function applyTranslations(root) {
    root.querySelectorAll("[data-i18n]").forEach(el => {
        const key = el.getAttribute("data-i18n");
        const text = state.translations[key];
        if (text) el.textContent = text;
    });
    // Плейсхолдеры полей ввода. Атрибут в разметке был, а обработки — нет:
    // узбекский и английский интерфейс показывали русскую подсказку в чате
    // (аудит 2026-09-03).
    root.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
        const text = state.translations[el.getAttribute("data-i18n-placeholder")];
        if (text) el.setAttribute("placeholder", text);
    });
    // Title тэг
    if (root === document) {
        document.title = t("app_title");
    }
}

// ============ DASHBOARD LOAD ============

async function loadDashboard() {
    try {
        const url = `/api/dashboard/${state.currentStudentId}?days=${state.currentDays}`;
        state.dashboard = await fetchJSON(url);
        renderDashboard();
    } catch (e) {
        console.error("Dashboard load failed", e);
        showError(t("error_generic") + ": " + e.message);
    }
}

function onPeriodChange(btn) {
    document.querySelectorAll(".period-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    state.currentDays = parseInt(btn.dataset.days, 10);
    state.quarters = null;  // период сменился — обнулить четверти
    // year report не зависит от периода — не обнуляем
    loadDashboard();
    // Если открыта вкладка «Итоги года» — перезагрузить и её, иначе на экране
    // остались бы цифры предыдущего ребёнка под именем текущего.
    if (state.currentView === "year") {
        _loadYearReportIfNeeded();
    }
}

function switchStudent(studentId) {
    state.currentStudentId = studentId;
    state.quarters = null;
    state.yearReport = null;  // сменился ребёнок — перезагрузим отчёт за год
    state.lastSeenAt = null;  // «новое» считается относительно нового ребёнка
    state._gradesGroupsShown = _GRADES_INITIAL_GROUPS;

    document.querySelectorAll(".student-tab").forEach(tab => {
        tab.classList.toggle("active", parseInt(tab.dataset.id, 10) === studentId);
    });

    loadDashboard();
}

// ============ RENDER ============

function renderGreeting(user) {
    const el = document.getElementById("greeting-text");
    if (user?.first_name) {
        el.textContent = t("greeting", { name: user.first_name });
    } else {
        el.textContent = t("greeting_no_name");
    }
}

function renderStudentTabs(students) {
    const wrap = document.getElementById("student-tabs");
    if (students.length <= 1) {
        wrap.classList.add("hidden");
        return;
    }
    wrap.classList.remove("hidden");
    wrap.innerHTML = students.map((s, i) => `
        <button class="student-tab ${i === 0 ? "active" : ""}" data-id="${s.id}">
            ${escapeHtml(s.display_name || s.fio)}
        </button>
    `).join("");
    wrap.querySelectorAll(".student-tab").forEach(tab => {
        tab.addEventListener("click", () => switchStudent(parseInt(tab.dataset.id, 10)));
    });
}

function renderDashboard() {
    // Radical refactor: analytical dashboard structure.
    // 1) KPIs row (4 cards) — заменил hero
    // 2) Status line — одна строка
    // 3) Quarters primary (data из основного response, не lazy)
    // 4) Multi-line trend by subject (заменил trend by day)
    // 5) All-subjects sortable table (click → drill-down)
    // 6) All-grades с фильтром по предмету
    // 7) Year report остаётся (auto-expand в апреле-июне)
    const d = state.dashboard;
    if (!d) return;

    renderKpis(d.kpis || {}, d.summary || {});
    renderStatusLine(d.summary || {});
    // Вкладка «Предметы»: список за выбранный период.
    renderSubjectsList(d.by_subject || [], d.trend_by_subject || []);
    // Четвертные с прогнозом годовой — вкладка «Итоги». Карточки по-прежнему
    // обогащены current-period статистикой, поэтому получают те же массивы.
    renderQuartersBlock(d.quarters_with_forecast || [], d.by_subject || []);
    state.availableYears = d.available_years || [];
    renderStaleBanner(d);

    // Учебный год четвертных подписан явно: карточка «1ч 3 · 2ч 4 · Год 3»
    // выглядит одинаково для текущего и прошлого года, а разница принципиальна.
    const yearBadge = document.getElementById("quarters-year-badge");
    if (yearBadge) {
        const label = d.quarters_academic_year_label;
        yearBadge.textContent = label ? ` · ${label}` : "";
        yearBadge.classList.toggle("hidden", !label);
    }
    // Снимок «когда родитель смотрел в прошлый раз» берём ДО рендера списка и
    // только один раз за загрузку дашборда — иначе бейдж «новое» гас сразу же.
    if (!state.lastSeenAt) {
        let stored = null;
        try {
            stored = localStorage.getItem(LAST_SEEN_KEY(state.currentStudentId));
        } catch (e) {
            stored = null;   // приватный режим / заблокированное хранилище
        }
        state.lastSeenAt = stored ? new Date(stored) : new Date(0);
    }

    renderAllGrades(d.recent_grades || []);

    // Отмечаем студента просмотренным — для подсветки «новое» в следующий заход.
    try {
        localStorage.setItem(LAST_SEEN_KEY(state.currentStudentId), new Date().toISOString());
    } catch (e) {
        // Telegram WebView может блокировать хранилище — подсветка не критична.
    }

    // Year report — теперь в отдельной tab (view-year), load lazy при switch.
    setupViewTabs();
}

// ═════════ ВКЛАДКИ (Сегодня / Предметы / Итоги / Чат) ═════════

// data-view кнопки нижней навигации → id панели.
const TAB_VIEWS = {
    today: "view-today",
    subjects: "view-subjects",
    year: "view-year",
    chat: "view-chat",
};

// Период (Неделя/Месяц/Четверть/Год) осмыслен только там, где числа считаются
// за скользящее окно. «Итоги» разрезаны по учебным годам, «Чат» — не про цифры.
const VIEWS_WITH_PERIOD = new Set(["today", "subjects"]);

function setupViewTabs() {
    // onclick вместо addEventListener: функция зовётся из каждого рендера
    // дашборда, и слушатели накапливались — после пяти перерисовок один клик
    // вызывал switchView пять раз (аудит 2026-09-03).
    document.querySelectorAll(".tabbar-btn").forEach(tab => {
        tab.onclick = () => switchView(tab.dataset.view);
    });
}

function switchView(view) {
    if (!TAB_VIEWS[view]) return;
    state.currentView = view;

    document.querySelectorAll(".tabbar-btn").forEach(t => {
        const isActive = t.dataset.view === view;
        t.classList.toggle("active", isActive);
        t.setAttribute("aria-selected", isActive ? "true" : "false");
    });

    Object.entries(TAB_VIEWS).forEach(([key, id]) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle("hidden", key !== view);
    });

    const periodToggle = document.getElementById("period-toggle");
    if (periodToggle) periodToggle.classList.toggle("hidden", !VIEWS_WITH_PERIOD.has(view));

    // Смена вкладки — это смена экрана: пользователь ждёт его начало, а не
    // прокрутку, оставшуюся от предыдущей вкладки.
    window.scrollTo({ top: 0, behavior: "instant" });

    if (view === "year") _loadYearReportIfNeeded();
}

async function _loadYearReportIfNeeded(year) {
    // year === undefined — год по умолчанию (год привязанной таблицы).
    // Явно выбранный год всегда перезагружаем: это другой набор данных.
    if (year === undefined && (state.yearReport || state.yearLoading)) {
        if (state.yearReport) renderYearReport();
        return;
    }
    if (state.yearLoading) return;
    state.yearLoading = true;
    document.getElementById("year-loading").classList.remove("hidden");
    document.getElementById("year-empty").classList.add("hidden");
    document.getElementById("year-content").classList.add("hidden");
    try {
        const q = year === undefined ? "" : `?year=${encodeURIComponent(year)}`;
        state.yearReport = await fetchJSON(`/api/dashboard/year/${state.currentStudentId}${q}`);
        renderYearReport();
    } catch (e) {
        console.warn("Year report load failed", e);
        document.getElementById("year-loading").classList.add("hidden");
        document.getElementById("year-empty").classList.remove("hidden");
    } finally {
        state.yearLoading = false;
    }
}

function renderYearPicker(report) {
    // Год подписан классом ТОГО года: «8 Orion · 2025/26». Класс живёт одним
    // перезаписываемым полем, поэтому без снимка прошлогодние оценки
    // подписывались бы текущим классом.
    const picker = document.getElementById("year-picker");
    const select = document.getElementById("year-select");
    if (!picker || !select) return;

    const years = (report && report.available_years) || [];
    // Один год — выбирать не из чего, прячем.
    picker.classList.toggle("hidden", years.length < 2);
    if (years.length < 2) return;

    select.innerHTML = "";
    years.forEach(y => {
        const opt = document.createElement("option");
        opt.value = y.academic_year;
        opt.textContent = y.display_name ? `${y.display_name} · ${y.label}` : y.label;
        select.appendChild(opt);
    });
    select.value = String(report.academic_year);
    select.onchange = () => _loadYearReportIfNeeded(select.value);
}

// ═════════ KPI ROW (4 cards) ═════════
function renderKpis(kpis, summary) {
    const avgEl = document.getElementById("kpi-avg");
    const deltaEl = document.getElementById("kpi-delta");
    const topNameEl = document.getElementById("kpi-top-name");
    const topAvgEl = document.getElementById("kpi-top-avg");
    const topCountEl = document.getElementById("kpi-top-count");
    const worstNameEl = document.getElementById("kpi-worst-name");
    const worstAvgEl = document.getElementById("kpi-worst-avg");
    const worstCountEl = document.getElementById("kpi-worst-count");
    const periodHintEl = document.getElementById("kpi-period-hint");

    // Подпись под средним баллом: сколько оценок и за какой срок он посчитан.
    // Оба числа в одной строке — балл без размера выборки и без периода
    // выглядит точнее, чем он есть. Плитка «Оценок за период» уехала сюда же.
    const days = kpis.period_days || summary.period_days || 30;
    if (periodHintEl) {
        const tpl = t("today_sample_tpl") || "{n} оценок за {days} дн.";
        periodHintEl.textContent = tpl
            .replace("{n}", String(kpis.total_grades ?? 0))
            .replace("{days}", String(days));
    }

    // Avg
    const avg = kpis.current_avg ?? summary.current_avg;
    if (avg == null) {
        avgEl.textContent = "—";
        avgEl.className = "today-hero-value muted";
        deltaEl.classList.add("hidden");
    } else {
        avgEl.textContent = avg.toFixed(2);
        avgEl.className = "today-hero-value " + gradeColorClass(avg);
        const delta = kpis.delta ?? summary.delta;
        if (delta != null && Math.abs(delta) >= 0.05) {
            deltaEl.classList.remove("hidden");
            deltaEl.classList.toggle("delta-up", delta > 0);
            deltaEl.classList.toggle("delta-down", delta < 0);
            deltaEl.textContent = `${delta > 0 ? "↑+" : "↓"}${Math.abs(delta).toFixed(2)}`;
        } else {
            deltaEl.classList.add("hidden");
        }
    }

    const fmtCount = (n) => {
        const tpl = t("kpi_subject_count_tpl") || "{n} оц.";
        return tpl.replace("{n}", String(n));
    };

    // Top — теперь с count badge, только если >=3 оценок (filter в compute_dashboard_kpis)
    if (kpis.top_subject) {
        topNameEl.textContent = kpis.top_subject.name;
        topAvgEl.textContent = kpis.top_subject.avg.toFixed(2);
        if (topCountEl) topCountEl.textContent = fmtCount(kpis.top_subject.count || 0);
    } else {
        topNameEl.textContent = "—";
        topAvgEl.textContent = "";
        if (topCountEl) topCountEl.textContent = t("kpi_no_data") || "недостаточно данных";
    }

    // Worst
    if (kpis.worst_subject) {
        worstNameEl.textContent = kpis.worst_subject.name;
        worstAvgEl.textContent = kpis.worst_subject.avg.toFixed(2);
        worstAvgEl.className = "kpi-value-secondary " + gradeColorClass(kpis.worst_subject.avg);
        if (worstCountEl) worstCountEl.textContent = fmtCount(kpis.worst_subject.count || 0);
    } else {
        worstNameEl.textContent = "—";
        worstAvgEl.textContent = "";
        if (worstCountEl) worstCountEl.textContent = t("kpi_no_data") || "недостаточно данных";
    }
}

function renderStatusLine(summary) {
    const el = document.getElementById("status-line");
    if (!el) return;
    if (summary.current_avg == null) {
        el.textContent = t("hero_no_grades_hint") || "";
        el.className = "status-line";
        return;
    }
    el.textContent = t(`status_${summary.status}`) || "";
    el.className = "status-line status-" + (summary.status || "stable");
}

// Dashboard refresh: SUGGESTED_PROMPTS, renderInsight, _openChatWithPrompt
// удалены. AI-фичи (insight + suggested prompts + chat) теперь только в
// боте — webapp дашборд только данные + Share/PDF.

// ═════════ QUARTERS BLOCK — enriched cards (единственный subject listing) ═════════
function renderStaleBanner(d) {
    // Таблица ученика относится к прошлому учебному году → монитор её не
    // читает. Экран при этом выглядит обычно, просто ничего не меняется, и
    // родитель делает вывод «оценок нет». Полоса объясняет, что происходит,
    // и ведёт прямо на смену ссылки.
    const banner = document.getElementById("stale-banner");
    if (!banner) return;
    const stale = !!(d && d.sheet_stale);
    banner.classList.toggle("hidden", !stale);
    if (!stale) return;

    const body = document.getElementById("stale-banner-body");
    if (body) {
        const year = d.academic_year
            ? `${d.academic_year}/${String(d.academic_year + 1).slice(-2)}`
            : "";
        body.textContent = (t("stale_banner_body") || "").replace("{year}", year);
    }
    const btn = document.getElementById("stale-banner-btn");
    if (btn) btn.onclick = () => _openBotRelink();
}

function _openBotRelink() {
    // Deep-link `/start relink` открывает в боте выбор ребёнка для смены ссылки.
    const tg = window.Telegram && window.Telegram.WebApp;
    const botUsername = window.GS_BOT_USERNAME || state.botUsername;
    if (!botUsername) {
        const hint = t("stale_banner_action") || "";
        if (tg && typeof tg.showAlert === "function") tg.showAlert(hint);
        return;
    }
    const url = `https://t.me/${botUsername}?start=relink`;
    if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
    if (tg && typeof tg.openTelegramLink === "function") {
        tg.openTelegramLink(url);
    } else {
        window.open(url, "_blank");
    }
}

// trendBySubject больше не нужен: спарклайны переехали во вкладку «Предметы».
// bySubject остался — по нему добираются предметы с текущими оценками, но без
// четвертных, иначе они пропали бы из итогов года совсем.
function renderQuartersBlock(quarters, bySubject) {
    const wrap = document.getElementById("quarters-table-wrap");
    const empty = document.getElementById("quarters-empty");
    if (!wrap) return;

    // Cards: сначала квартирные subjects, потом current-only (новые предметы
    // у которых есть текущие оценки но нет четвертных)
    const quarterSubjects = new Set((quarters || []).map(q => q.subject));
    const currentOnly = (bySubject || [])
        .filter(s => !quarterSubjects.has(s.name))
        .map(s => ({ subject: s.name, _no_quarters: true }));
    const allCards = [...(quarters || []), ...currentOnly];

    if (allCards.length === 0) {
        wrap.innerHTML = "";
        if (empty) empty.classList.remove("hidden");
        return;
    }
    if (empty) empty.classList.add("hidden");

    const cell = (val, isForecast) => {
        if (val == null || val === '') return `<span class="qc-grade muted">—</span>`;
        const cls = isForecast ? "qc-grade forecast" : "qc-grade";
        return `<span class="${cls}">${escapeHtml(String(val))}</span>`;
    };

    const cards = allCards.map(q => {
        // Тренд словом, а не стрелкой: «↓» родитель читает как «плохо вообще»,
        // а «снижается» — как «от четверти к четверти», что и имеется в виду.
        const trendKey = q.trend === 'up' ? 'quarters_trend_up'
            : q.trend === 'down' ? 'quarters_trend_down' : 'quarters_trend_flat';
        const trendLabel = t(trendKey) || '';
        const trendCls = q.trend === 'up' ? 'trend-up' : q.trend === 'down' ? 'trend-down' : 'trend-flat';
        // Honesty: для прогноза показываем число + явный badge «прогноз»,
        // не маскируем под точное значение «~3.2».
        let yearVal = q.year || '—';
        let yearBadge = '';
        if (q.year_is_forecast && q.year_value != null) {
            yearVal = q.year_value.toFixed(1);
            yearBadge = `<span class="qc-forecast-badge" data-i18n="quarters_forecast_badge">прогноз</span>`;
        }
        const yearCls = q.year_is_forecast ? 'qc-year-value forecast' : 'qc-year-value';
        const yearColorCls = q.year_value != null ? gradeColorClass(q.year_value) : '';

        // Quarter cells (если нет четвертных — empty placeholders)
        const quartersHtml = q._no_quarters
            ? `<div class="qc-quarters qc-quarters-empty">
                 <span class="muted small">${escapeHtml(t("quarters_no_data") || "Нет четвертных оценок")}</span>
               </div>`
            // Подписи четвертей — из локалей: ключи quarter_1..quarter_4 давно
            // лежали в ru/uz/en, а в разметке было захардкожено «1ч/2ч/3ч/4ч».
            : `<div class="qc-quarters">
                 <div class="qc-q"><span class="qc-q-label">${escapeHtml(t("quarter_1") || "1ч")}</span>${cell(q.q1)}</div>
                 <div class="qc-q"><span class="qc-q-label">${escapeHtml(t("quarter_2") || "2ч")}</span>${cell(q.q2)}</div>
                 <div class="qc-q"><span class="qc-q-label">${escapeHtml(t("quarter_3") || "3ч")}</span>${cell(q.q3)}</div>
                 <div class="qc-q"><span class="qc-q-label">${escapeHtml(t("quarter_4") || "4ч")}</span>${cell(q.q4)}</div>
               </div>`;

        // Year column — only if quarter has it
        const yearHtml = q._no_quarters ? '' : `<div class="qc-year">
            <span class="qc-year-label">${escapeHtml(t("col_year") || "Год")}</span>
            <span class="${yearCls} ${yearColorCls}">${escapeHtml(yearVal)}</span>
            ${yearBadge}
        </div>`;

        // Средний за период и спарклайн из подвала убраны: ровно это лежит
        // строкой во вкладке «Предметы», и на двух вкладках одно и то же число
        // расходилось бы при разных периодах.
        return `<div class="quarter-card" data-subject="${escapeHtml(q.subject)}">
            <div class="qc-header">
                <span class="qc-subject">${escapeHtml(q.subject)}</span>
                <span class="qc-trend ${trendCls}">${escapeHtml(trendLabel)}</span>
            </div>
            <div class="qc-body">
                ${quartersHtml}
                ${yearHtml}
            </div>
        </div>`;
    }).join("");

    wrap.innerHTML = `<div class="quarter-cards">${cards}</div>
        <p class="qr-note muted">${escapeHtml(t("quarters_forecast_note") || "")}</p>`;

    wrap.querySelectorAll(".quarter-card").forEach(card => {
        card.addEventListener("click", () => openDrilldown(card.dataset.subject));
    });
}

// ═════════ ALL SUBJECTS TABLE (sortable + clickable + sparkline) ═════════
function _sparklineSvg(points, width, height) {
    if (!points || points.length < 2) {
        return `<svg width="${width}" height="${height}" aria-hidden="true"></svg>`;
    }
    const values = points.map(p => p.avg);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = Math.max(0.5, max - min);  // min range — иначе flat line из-за 1 точки
    const dx = (width - 2) / (points.length - 1);
    const pts = points.map((p, i) => {
        const x = 1 + i * dx;
        const y = height - 2 - ((p.avg - min) / range) * (height - 4);
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    return `<svg class="sparkline" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">
        <path d="${pts}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
}

// ═════════ ALL GRADES (grouped by date + subject filter + "show more") ═════════
// Chart.js (205 КБ raw / 73 КБ gzip) нужен ровно одному экрану — графику в
// drill-down по предмету. Раньше он висел в <head> и составлял ~69 % трафика
// первой загрузки у каждого родителя, включая тех, кто drill-down не открывает.
let _chartJsPromise = null;
function _ensureChartJs() {
    if (window.Chart) return Promise.resolve(true);
    if (!_chartJsPromise) {
        _chartJsPromise = new Promise(resolve => {
            const el = document.createElement("script");
            el.src = "/static/vendor/chart.umd.min.js";
            el.onload = () => resolve(true);
            el.onerror = () => { _chartJsPromise = null; resolve(false); };
            document.head.appendChild(el);
        });
    }
    return _chartJsPromise;
}

const _GRADES_INITIAL_GROUPS = 7;  // показываем последние 7 дат

// Локальная дата в 'YYYY-MM-DD'. Ученик, бот и сервер живут в Ташкенте, поэтому
// сравниваем календарные дни как строки, а не через Date: `new Date("2026-09-03")`
// разбирается как UTC-полночь, и при UTC+5 «сегодня» уезжало на вчерашние оценки.
function _todayIso() {
    const n = new Date();
    return `${n.getFullYear()}-${String(n.getMonth()+1).padStart(2,'0')}-${String(n.getDate()).padStart(2,'0')}`;
}

function _monthLabel(m) {
    // Названия месяцев — из локалей. Сервер отдаёт month_num/year и русский
    // label лишь как запасной вариант для старых клиентов.
    const key = `month_${m.month_num}`;
    const name = t(key);
    return name && m.year ? `${name} ${m.year}` : (m.label || "");
}

function _formatDateGroupLabel(dateStr) {
    if (!dateStr) return '?';
    // dateStr всегда 'YYYY-MM-DD' (сервер нормализует в _serialize_grades).
    const [y, m, day] = dateStr.split('-').map(Number);
    if (!y || !m || !day) return dateStr;
    const d = new Date(y, m - 1, day);          // локальная полночь, без сдвига
    const todayIso = _todayIso();
    const [ty, tm, td] = todayIso.split('-').map(Number);
    const today = new Date(ty, tm - 1, td);
    const diffDays = Math.round((today - d) / (24 * 3600 * 1000));
    if (diffDays === 0) return t("grades_today") || "Сегодня";
    if (diffDays === 1) return t("grades_yesterday") || "Вчера";
    if (diffDays < 7) {
        const days = [
            t("dow_sun"), t("dow_mon"), t("dow_tue"), t("dow_wed"),
            t("dow_thu"), t("dow_fri"), t("dow_sat"),
        ];
        return `${days[d.getDay()] || ''}, ${d.getDate()}.${String(d.getMonth()+1).padStart(2,'0')}`;
    }
    return `${d.getDate()}.${String(d.getMonth()+1).padStart(2,'0')}`;
}

// «04.09» — дата группы, которую метка («Сегодня», «Вчера», «Вт») не называет.
function _formatDayNumeric(dateStr) {
    if (!dateStr) return "";
    const [, m, d] = dateStr.split("-").map(Number);
    if (!m || !d) return "";
    return `${String(d).padStart(2, "0")}.${String(m).padStart(2, "0")}`;
}

function renderAllGrades(grades) {
    const list = document.getElementById("recent-list");
    const truncatedEl = document.getElementById("recent-truncated");
    if (!list) return;

    // Сервер отдаёт срез. Молчать об этом нельзя — иначе родитель решит, что
    // оценок ровно столько, сколько он видит. Строка появляется только когда
    // срез действительно меньше целого.
    const shown = grades.length;
    const total = (state.dashboard && state.dashboard.recent_total) || shown;
    if (truncatedEl) {
        const isTruncated = total > shown;
        truncatedEl.classList.toggle("hidden", !isTruncated);
        if (isTruncated) {
            const tpl = t("grades_truncated_tpl") || "Показаны последние {n} из {total}";
            truncatedEl.textContent = tpl
                .replace("{n}", String(shown))
                .replace("{total}", String(total));
        }
    }

    const filtered = grades;

    if (filtered.length === 0) {
        list.innerHTML = `<p class="empty-hint">${t("hero_no_grades")}</p>`;
        return;
    }

    // Группировка по дате (DESC — свежие сверху)
    const byDate = new Map();
    filtered.forEach(g => {
        const date = g.grade_date || (g.date_added ? g.date_added.slice(0, 10) : '');
        if (!byDate.has(date)) byDate.set(date, []);
        byDate.get(date).push(g);
    });
    const sortedDates = Array.from(byDate.keys()).sort().reverse();

    if (!state._gradesGroupsShown) state._gradesGroupsShown = _GRADES_INITIAL_GROUPS;
    const visibleDates = sortedDates.slice(0, state._gradesGroupsShown);

    // lastSeen фиксируется один раз на загрузку дашборда (см. renderDashboard).
    // Раньше значение читалось из localStorage уже ПОСЛЕ того, как тот же рендер
    // записал туда «сейчас», поэтому бейдж «новое» исчезал после первого же
    // переключения периода.
    const lastSeen = state.lastSeenAt instanceof Date ? state.lastSeenAt : new Date(0);

    const groupsHtml = visibleDates.map(date => {
        const dayGrades = byDate.get(date);
        const rows = dayGrades.map(g => {
            const isNew = g.date_added && new Date(g.date_added) > lastSeen;
            const colorClass = g.grade_value !== null ? gradeColorClass(g.grade_value) : "grade-text";
            const value = g.raw_text || (g.grade_value !== null ? g.grade_value : "—");
            const newBadge = isNew ? `<span class="badge-new">${t("badge_new") || "new"}</span>` : "";
            return `<div class="g-row ${isNew ? "is-new" : ""}">
                <span class="g-subject">${escapeHtml(g.subject)}${newBadge}</span>
                <span class="g-grade ${colorClass}">${escapeHtml(String(value))}</span>
            </div>`;
        }).join("");
        // Метка слева («Сегодня», «Вчера», день недели), дата справа. Дата
        // числовая: названия месяцев в локалях именительные, и «4 Сентябрь»
        // было бы безграмотно во всех трёх языках.
        return `<div class="g-group">
            <div class="g-group-header">
                <span class="g-group-label">${escapeHtml(_formatDateGroupLabel(date))}</span>
                <span class="g-group-date">${escapeHtml(_formatDayNumeric(date))}</span>
            </div>
            <div class="g-group-rows">${rows}</div>
        </div>`;
    }).join("");

    const remaining = sortedDates.length - visibleDates.length;
    const moreBtn = remaining > 0
        ? `<button id="g-show-more" class="btn-show-more" type="button">${escapeHtml(t("grades_show_more") || "Показать ещё")} (${remaining})</button>`
        : '';

    list.innerHTML = groupsHtml + moreBtn;

    const moreEl = document.getElementById("g-show-more");
    if (moreEl) {
        moreEl.addEventListener("click", () => {
            state._gradesGroupsShown += 7;
            renderAllGrades(grades);
        });
    }
}

// ═════════ DRILL-DOWN by SUBJECT ═════════
function openDrilldown(subject) {
    if (!subject) return;
    window.location.hash = `subject/${encodeURIComponent(subject)}`;
}

function renderDrilldown(subject) {
    const d = state.dashboard;
    if (!d) return;

    const grades = (d.recent_grades || []).filter(g => g.subject === subject);
    const subj = (d.by_subject || []).find(s => s.name === subject);

    document.getElementById("drilldown-title").textContent = subject;
    document.getElementById("dd-avg").textContent = subj ? subj.avg.toFixed(2) : "—";
    document.getElementById("dd-avg").className = "kpi-value " + (subj ? gradeColorClass(subj.avg) : "muted");
    document.getElementById("dd-count").textContent = subj ? subj.count : grades.length;
    const trendEl = document.getElementById("dd-trend");
    if (subj) {
        trendEl.textContent = subj.trend === 'up' ? '↑' : subj.trend === 'down' ? '↓' : '→';
        trendEl.className = "kpi-value " + (subj.trend === 'up' ? 'trend-up' : subj.trend === 'down' ? 'trend-down' : 'trend-flat');
    } else {
        trendEl.textContent = "—";
    }

    // Chart по этому предмету (line)
    const ctx = document.getElementById("ddChart")?.getContext("2d");
    if (state.ddChart) { state.ddChart.destroy(); state.ddChart = null; }
    if (ctx && grades.length > 1) {
        // Библиотека подгружается только здесь — на главном экране она не нужна.
        _ensureChartJs().then(ok => {
            if (!ok) return;
        const sorted = grades.slice().sort((a, b) => {
            const da = a.grade_date || a.date_added || '';
            const db = b.grade_date || b.date_added || '';
            return da < db ? -1 : da > db ? 1 : 0;
        });
        state.ddChart = new Chart(ctx, {
            type: "line",
            data: {
                labels: sorted.map(g => (g.grade_date || g.date_added || '').slice(5, 10)),
                datasets: [{
                    label: subject,
                    data: sorted.map(g => g.grade_value),
                    borderColor: "#6366F1",
                    backgroundColor: "rgba(99,102,241,0.12)",
                    fill: true, tension: 0.3, pointRadius: 4,
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: { y: { min: 1, max: 5, ticks: { stepSize: 1 } } },
            },
            });
        });
    }

    // List all grades (chronological DESC)
    const listEl = document.getElementById("dd-grades-list");
    if (listEl) {
        listEl.innerHTML = grades.slice().sort((a, b) => {
            const da = a.grade_date || a.date_added || '';
            const db = b.grade_date || b.date_added || '';
            return db < da ? -1 : db > da ? 1 : 0;
        }).map(g => {
            const colorClass = g.grade_value !== null ? gradeColorClass(g.grade_value) : "grade-text";
            const value = g.raw_text || g.grade_value || '—';
            const date = g.grade_date || (g.date_added ? g.date_added.slice(0, 10) : '');
            return `<div class="recent-row">
                <span class="recent-date">${escapeHtml(date)}</span>
                <span class="recent-subject">${escapeHtml(subject)}</span>
                <span class="recent-grade ${colorClass}">${escapeHtml(String(value))}</span>
            </div>`;
        }).join("");
    }

    // AI deep-link with pre-filled question про этот предмет
    const askBtn = document.getElementById("dd-btn-ask-ai");
    // askAiAboutSubject — закрывает drill-down + pre-fills+sends в inline chat
    if (askBtn) {
        askBtn.onclick = () => askAiAboutSubject(subject);
    }
}

function closeDrilldown() {
    window.location.hash = '';
}

function _handleHashChange() {
    const hash = window.location.hash.slice(1);
    if (hash.startsWith('subject/')) {
        const subject = decodeURIComponent(hash.slice('subject/'.length));
        hide("content");
        show("drilldown");
        renderDrilldown(subject);
    } else {
        hide("drilldown");
        show("content");
    }
}

function _openBotChatWithQuestion(question) {
    // Real deep-link через t.me/<bot>?start=ai_<base64(question)>.
    // Bot handler /start ai_X декодирует и сразу шлёт question в AI.
    // bot_username priority: server-injected window.GS_BOT_USERNAME (всегда
    // current при reload) → state.botUsername (cached из /api/init).
    // Раньше state.botUsername мог быть null из-за init race → fallback
    // popup без deep-link → AI "не работала".
    const tg = window.Telegram && window.Telegram.WebApp;
    const botUsername = window.GS_BOT_USERNAME || state.botUsername;

    if (!botUsername) {
        const hint = t("ai_popup_general") || "Откройте бот и нажмите 💬 Чат";
        if (tg && typeof tg.showAlert === "function") {
            tg.showAlert(hint, () => { if (tg.close) tg.close(); });
        } else {
            alert(hint);
        }
        return;
    }

    const payload = question
        ? btoa(unescape(encodeURIComponent(question)))
            .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
        : '';
    const url = `https://t.me/${botUsername}?start=ai_${payload}`;

    if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
    if (tg && typeof tg.openTelegramLink === "function") {
        tg.openTelegramLink(url);
    } else {
        window.open(url, "_blank");
    }
}

// ============ YEAR REPORT — LAZY ============

function renderYearReport() {
    const report = state.yearReport;
    document.getElementById("year-loading").classList.add("hidden");
    // Селектор рисуем ДО проверки на пустоту: иначе из пустого года
    // не выбраться обратно.
    renderYearPicker(report);

    if (!report || report.numeric_count < 1) {
        document.getElementById("year-empty").classList.remove("hidden");
        return;
    }

    document.getElementById("year-content").classList.remove("hidden");

    // KPI cards (year view)
    const avgEl = document.getElementById("year-avg");
    avgEl.textContent = report.year_avg !== null ? report.year_avg.toFixed(2) : "—";
    if (report.year_avg !== null) avgEl.className = "kpi-value " + gradeColorClass(report.year_avg);

    document.getElementById("year-total-grades").textContent = report.numeric_count;

    const growthEl = document.getElementById("year-growth");
    if (report.growth !== null && report.growth !== undefined) {
        const sign = report.growth > 0 ? "+" : "";
        growthEl.textContent = `${sign}${report.growth}`;
    } else {
        growthEl.textContent = "—";
    }

    document.getElementById("year-streak").textContent = report.best_streak || 0;

    if (report.best_month) {
        document.getElementById("year-best-month").textContent =
            `${_monthLabel(report.best_month)} · ${report.best_month.avg.toFixed(2)}`;
    }
    if (report.worst_month) {
        document.getElementById("year-worst-month").textContent =
            `${_monthLabel(report.worst_month)} · ${report.worst_month.avg.toFixed(2)}`;
    }
}

// ═════════ ВКЛАДКА «ПРЕДМЕТЫ»: список за выбранный период ═════════
// Раньше эта функция существовала, но не вызывалась ниоткуда (мёртвый код с
// прошлого рефакторинга) — теперь она и есть содержимое вкладки.
function renderSubjectsList(bySubject, trendBySubject) {
    const list = document.getElementById("subjects-list");
    const empty = document.getElementById("subjects-empty");
    const countEl = document.getElementById("subjects-count");
    const sampleEl = document.getElementById("subjects-sample");
    if (!list) return;

    const subjects = [...(bySubject || [])].sort((a, b) => b.avg - a.avg);

    if (subjects.length === 0) {
        list.innerHTML = "";
        if (empty) empty.classList.remove("hidden");
        if (countEl) countEl.textContent = "";
        if (sampleEl) sampleEl.textContent = "";
        return;
    }
    if (empty) empty.classList.add("hidden");

    // Шапка списка: сколько предметов и на скольких оценках это посчитано.
    const totalGrades = subjects.reduce((sum, s) => sum + (s.count || 0), 0);
    const days = state.currentDays;
    if (countEl) {
        countEl.textContent = (t("subjects_count_tpl") || "{n} предметов")
            .replace("{n}", String(subjects.length));
    }
    if (sampleEl) {
        sampleEl.textContent = (t("today_sample_tpl") || "{n} оценок за {days} дн.")
            .replace("{n}", String(totalGrades))
            .replace("{days}", String(days));
    }

    const trendMap = new Map();
    (trendBySubject || []).forEach(line => trendMap.set(line.subject, line.points));

    list.innerHTML = subjects.map(s => {
        const spark = _sparklineSvg(trendMap.get(s.name), 64, 22);
        // Счётчик оценок — не украшение: средний по двум оценкам и по двадцати
        // читается одинаково, если не написать, сколько их было.
        const count = (t("subjects_grades_tpl") || "{n} оц.").replace("{n}", String(s.count || 0));
        return `<button class="subject-row" type="button" data-subject="${escapeHtml(s.name)}">
            <span class="subject-row-main">
                <span class="subject-name">${escapeHtml(s.name)}</span>
                <span class="subject-count">${escapeHtml(count)}</span>
            </span>
            <span class="subject-spark ${gradeColorClass(s.avg)}">${spark}</span>
            <span class="subject-avg ${gradeColorClass(s.avg)}">${s.avg.toFixed(2)}</span>
            <span class="subject-chevron" aria-hidden="true">›</span>
        </button>`;
    }).join("");

    list.querySelectorAll(".subject-row").forEach(row => {
        row.addEventListener("click", () => openDrilldown(row.dataset.subject));
    });
}

// ============ COLLAPSIBLE ============

function toggleSection(section) {
    const isOpen = section.classList.toggle("open");
    const btn = section.querySelector(".toggle-btn");
    btn.setAttribute("aria-expanded", isOpen ? "true" : "false");
    if (isOpen) {
        // Кастомный event для lazy-load четвертей
        section.dispatchEvent(new CustomEvent("toggle:open"));
    }
}

// ============ HELPERS ============

function show(id) { document.getElementById(id)?.classList.remove("hidden"); }
function hide(id) { document.getElementById(id)?.classList.add("hidden"); }

function showError(msg) {
    hide("skeleton");
    hide("content");
    show("error");
    document.getElementById("error-text").textContent = msg;
}

function gradeColorClass(avg) {
    if (avg >= 4.5) return "grade-good";
    if (avg >= 3.5) return "grade-ok";
    if (avg >= 2.5) return "grade-warn";
    return "grade-bad";
}

function getThemeColor(varName, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    return v || fallback;
}

function hexToRgba(hex, alpha) {
    // Поддерживаем #abc, #abcdef и rgb()-строки
    if (hex.startsWith("rgb")) return hex.replace("rgb(", "rgba(").replace(")", `, ${alpha})`);
    let h = hex.replace("#", "");
    if (h.length === 3) h = h.split("").map(c => c + c).join("");
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function formatDateShort(dateStr) {
    if (!dateStr) return "";
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    const locale = state.lang === "uz" ? "uz-UZ" : state.lang === "en" ? "en-GB" : "ru-RU";
    return d.toLocaleDateString(locale, { day: "2-digit", month: "short" });
}

function formatPeriod(startStr, endStr) {
    if (!startStr || !endStr) return "";
    const s = new Date(startStr);
    const e = new Date(endStr);
    const locale = state.lang === "uz" ? "uz-UZ" : state.lang === "en" ? "en-GB" : "ru-RU";
    const fmt = (d) => d.toLocaleDateString(locale, { day: "numeric", month: "short" });
    return `${fmt(s)} – ${fmt(e)}`;
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = String(text ?? "");
    return div.innerHTML;
}

// ============ ACTION BAR (Dashboard refresh) ============

function setupActionBar() {
    const pdfBtn = document.getElementById("btn-export-pdf");
    if (pdfBtn) pdfBtn.addEventListener("click", handleExportPdf);
    // Кнопки «Спросить AI» больше нет — чат стал вкладкой нижней навигации.
    setupInlineChat();
}

// Открыть чат. Раньше это был скролл к секции внутри одного длинного экрана,
// теперь чат — отдельная вкладка, поэтому переключаем её.
function _scrollToChat() {
    switchView("chat");
    const input = document.getElementById("chat-input");
    if (input) setTimeout(() => input.focus(), 200);
}

// ═════════ INLINE AI CHAT (revert удаления — теперь в Mini App) ═════════
function setupInlineChat() {
    const input = document.getElementById("chat-input");
    const sendBtn = document.getElementById("chat-send");
    const clearBtn = document.getElementById("chat-clear");
    if (!input || !sendBtn) return;

    document.getElementById("chat-history").innerHTML = "";
    state.chatHistoryLoaded = false;
    if (clearBtn) clearBtn.classList.add("hidden");

    const send = () => _sendChatMessage(input.value.trim());
    sendBtn.onclick = send;
    input.onkeydown = (e) => { if (e.key === "Enter") send(); };

    if (clearBtn) clearBtn.onclick = _clearChatHistory;

    _loadChatHistory();
}

async function _loadChatHistory() {
    if (state.chatHistoryLoaded) return;
    if (!state.currentStudentId) return;
    state.chatHistoryLoaded = true;
    try {
        const data = await fetchJSON(`/api/chat/history/${state.currentStudentId}`);
        const messages = data.messages || [];
        if (messages.length === 0) return;
        messages.forEach(m => {
            const role = m.role === "user" ? "user" : "ai";
            const node = _appendChatMessage(role, m.content);
            if (role === "ai" && m.id != null && node) _attachFeedbackToNode(node, m.id);
        });
        _markChatHasHistory();
    } catch (e) {
        console.warn("Chat history load failed", e);
    }
}

function _appendChatMessage(role, text) {
    const container = document.getElementById("chat-history");
    if (!container) return null;
    const div = document.createElement("div");
    div.className = `chat-msg chat-msg-${role}`;
    div.textContent = text;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
}

function _attachFeedbackToNode(bubbleNode, messageId) {
    if (bubbleNode.nextSibling && bubbleNode.nextSibling.classList &&
        bubbleNode.nextSibling.classList.contains("chat-feedback-row")) return;
    const row = document.createElement("div");
    row.className = "chat-feedback-row";
    row.dataset.messageId = String(messageId);
    const upLabel = t("chat_feedback_up_label") || "Helpful";
    const downLabel = t("chat_feedback_down_label") || "Not helpful";
    const up = document.createElement("button");
    up.type = "button"; up.className = "chat-fb-btn"; up.dataset.rating = "1";
    up.textContent = "👍"; up.setAttribute("aria-label", upLabel);
    const down = document.createElement("button");
    down.type = "button"; down.className = "chat-fb-btn"; down.dataset.rating = "-1";
    down.textContent = "👎"; down.setAttribute("aria-label", downLabel);
    up.addEventListener("click", () => _sendFeedback(row, messageId, 1));
    down.addEventListener("click", () => _sendFeedback(row, messageId, -1));
    row.appendChild(up); row.appendChild(down);
    bubbleNode.parentNode.insertBefore(row, bubbleNode.nextSibling);
}

async function _sendFeedback(rowNode, messageId, rating) {
    try {
        const res = await fetch("/api/chat/feedback", {
            method: "POST",
            headers: { "Content-Type": "application/json", ...API_HEADERS },
            body: JSON.stringify({ message_id: messageId, rating }),
        });
        if (!res.ok) { console.warn("Feedback failed", res.status); return; }
    } catch (e) { console.warn("Feedback network failed", e); return; }
    rowNode.querySelectorAll(".chat-fb-btn").forEach(btn => {
        btn.classList.toggle("selected", parseInt(btn.dataset.rating, 10) === rating);
        btn.disabled = true;
    });
    const existing = rowNode.querySelector(".chat-fb-thanks");
    if (existing) existing.remove();
    const thanks = document.createElement("span");
    thanks.className = "chat-fb-thanks";
    thanks.textContent = t("chat_feedback_thanks") || "Thanks";
    rowNode.appendChild(thanks);
    setTimeout(() => { if (thanks.parentNode) thanks.remove(); }, 2500);
}

function _markChatHasHistory() {
    const clearBtn = document.getElementById("chat-clear");
    if (clearBtn) clearBtn.classList.remove("hidden");
}

async function _sendChatMessage(question) {
    if (!question) return;
    if (!state.currentStudentId) return;
    if (state.chatBusy) return;
    state.chatBusy = true;
    const input = document.getElementById("chat-input");
    const sendBtn = document.getElementById("chat-send");
    if (input) { input.value = ""; input.disabled = true; }
    if (sendBtn) sendBtn.disabled = true;

    _appendChatMessage("user", question);
    const thinking = _appendChatMessage("ai", t("chat_thinking") || "AI думает…");
    if (thinking) thinking.classList.add("chat-thinking");

    try {
        const res = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json", ...API_HEADERS },
            body: JSON.stringify({ student_id: state.currentStudentId, question }),
        });
        if (res.status === 429) {
            if (thinking) {
                thinking.textContent = t("chat_rate_limited") || "Слишком много запросов";
                thinking.classList.remove("chat-thinking");
                thinking.classList.add("chat-error-msg");
            }
            return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (thinking) {
            thinking.textContent = data.answer || (t("chat_error") || "Ошибка");
            thinking.classList.remove("chat-thinking");
            if (!data.answer) thinking.classList.add("chat-error-msg");
            else if (data.message_id != null) _attachFeedbackToNode(thinking, data.message_id);
        }
        _markChatHasHistory();
    } catch (e) {
        console.warn("Chat failed", e);
        if (thinking) {
            thinking.textContent = t("chat_error") || "Ошибка соединения";
            thinking.classList.remove("chat-thinking");
            thinking.classList.add("chat-error-msg");
        }
    } finally {
        state.chatBusy = false;
        if (input) { input.disabled = false; input.focus(); }
        if (sendBtn) sendBtn.disabled = false;
    }
}

async function _clearChatHistory() {
    if (!window.confirm(t("chat_clear_confirm") || "Очистить историю?")) return;
    try {
        await fetch(`/api/chat/clear/${state.currentStudentId}`,
                    { method: "POST", headers: API_HEADERS });
    } catch (e) { console.warn("Clear failed", e); return; }
    document.getElementById("chat-history").innerHTML = "";
    const clearBtn = document.getElementById("chat-clear");
    if (clearBtn) clearBtn.classList.add("hidden");
}

// Внешний API для drill-down «спросить про предмет»
function askAiAboutSubject(subject) {
    closeDrilldown();
    setTimeout(() => {
        _scrollToChat();
        const input = document.getElementById("chat-input");
        if (input) input.value = `Расскажи про ${subject}`;
        setTimeout(() => _sendChatMessage(`Расскажи про ${subject}`), 300);
    }, 200);
}

// Dashboard refactor: _buildShareText / handleShare удалены — Share use case
// слабый (см. user feedback). PDF (через bot) теперь основной way делиться
// данными.

// PDF: показываем модалку с выбором типа отчёта вместо instant-генерации
function handleExportPdf() {
    const modal = document.getElementById("pdf-modal");
    if (!modal) return;

    // Список предметов пересобираем при каждом открытии: он заполнялся один
    // раз и после смены ребёнка предлагал предметы предыдущего.
    const subjSel = document.getElementById("pdf-subject-select");
    if (subjSel) {
        const previous = subjSel.value;
        subjSel.innerHTML = "";
        (state.dashboard?.by_subject || []).forEach(s => {
            const opt = document.createElement("option");
            opt.value = s.name; opt.textContent = s.name;
            subjSel.appendChild(opt);
        });
        if (previous) subjSel.value = previous;
    }

    // Период: учебные годы вместо окна в днях. Для выпускника важен разрез по
    // классам и сводка за все годы — с ней видно, с чем ребёнок идёт к выпуску.
    const yearSel = document.getElementById("pdf-year-select");
    if (yearSel) {
        const years = state.availableYears || [];
        yearSel.innerHTML = "";
        const current = document.createElement("option");
        current.value = "";
        current.textContent = t("pdf_period_current") || "Текущий период";
        yearSel.appendChild(current);
        years.forEach(y => {
            const opt = document.createElement("option");
            opt.value = String(y.academic_year);
            opt.textContent = y.display_name ? `${y.display_name} · ${y.label}` : y.label;
            yearSel.appendChild(opt);
        });
        if (years.length > 1) {
            const all = document.createElement("option");
            all.value = "all";
            all.textContent = t("pdf_period_all_years") || "Все годы обучения";
            yearSel.appendChild(all);
        }
    }

    // Toggle subject dropdown visibility по radio
    modal.querySelectorAll('input[name="pdf-type"]').forEach(radio => {
        radio.addEventListener("change", () => {
            const isSubject = modal.querySelector('input[name="pdf-type"]:checked').value === 'subject';
            if (subjSel) subjSel.classList.toggle("hidden", !isSubject);
        });
    });

    const closeFn = () => modal.classList.add("hidden");
    document.getElementById("pdf-modal-close").onclick = closeFn;
    document.getElementById("pdf-modal-cancel").onclick = closeFn;
    document.getElementById("pdf-modal-generate").onclick = () => {
        closeFn();
        const type = modal.querySelector('input[name="pdf-type"]:checked').value;
        const subject = subjSel ? subjSel.value : '';
        const year = yearSel ? yearSel.value : '';
        _sendPdfRequest(type, subject, year);
    };

    modal.classList.remove("hidden");
}

async function _sendPdfRequest(reportType, subject, academicYear) {
    const studentId = state.currentStudentId;
    const days = state.currentDays || 30;

    const pdfBtn = document.getElementById("btn-export-pdf");
    const originalText = pdfBtn ? pdfBtn.textContent : "";
    if (pdfBtn) {
        pdfBtn.disabled = true;
        pdfBtn.textContent = "⏳ " + (t("action_export_loading") || "PDF…");
    }

    const params = new URLSearchParams({ days: String(days), type: reportType });
    if (subject) params.set("subject", subject);
    if (academicYear) params.set("year", academicYear);

    try {
        const res = await fetch(
            `/api/dashboard/${studentId}/pdf/send?${params.toString()}`,
            { method: "POST", headers: API_HEADERS },
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const tg = window.Telegram && window.Telegram.WebApp;
        if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");

        // Показываем popup «отправлено в бот» через Telegram WebApp API
        // (нативный UI) с fallback на alert если SDK не доступен.
        const msg = t("action_export_sent") || "📄 PDF отправлен в чат с ботом";
        if (tg && typeof tg.showPopup === "function") {
            tg.showPopup({ message: msg, buttons: [{ type: "ok" }] }, () => {
                // После закрытия popup'а закрываем WebApp чтобы юзер увидел
                // файл в чате с ботом.
                if (tg.close) tg.close();
            });
        } else {
            alert(msg);
            if (tg && tg.close) tg.close();
        }
    } catch (e) {
        console.warn("PDF send failed", e);
        alert(t("action_export_error") || "Не удалось создать PDF");
    } finally {
        if (pdfBtn) {
            pdfBtn.disabled = false;
            pdfBtn.textContent = originalText;
        }
    }
}
