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
    currentDays: 7,
    dashboard: null,            // последний загруженный snapshot
    quarters: null,             // lazy-loaded
    quartersLoading: false,
    yearReport: null,           // lazy-loaded, end-of-year отчёт
    yearLoading: false,
    trendChart: null,
};

// localStorage ключ для last-seen timestamp (подсветка "новое")
const LAST_SEEN_KEY = (studentId) => `gs_lastseen_${studentId}`;

// ============ INIT ============

if (tg) {
    tg.ready();
    tg.expand();
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
        const res = await fetch(`/static/locales/${lang}.json`, { cache: "no-cache" });
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
}

function switchStudent(studentId) {
    state.currentStudentId = studentId;
    state.quarters = null;
    state.yearReport = null;  // сменился ребёнок — перезагрузим отчёт за год

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
    // Quarter cards enriched: current-period avg+count+sparkline для каждого
    // предмета. Это единственный listing — старая «Все предметы» удалена.
    renderQuartersBlock(
        d.quarters_with_forecast || [],
        d.by_subject || [],
        d.trend_by_subject || [],
    );
    renderAllGrades(d.recent_grades || []);

    // Mark студента как просмотренного — для подсветки "новое" в следующий заход
    localStorage.setItem(LAST_SEEN_KEY(state.currentStudentId), new Date().toISOString());

    // Year report — теперь в отдельной tab (view-year), load lazy при switch.
    setupViewTabs();
}

// ═════════ VIEW TABS (Дашборд / Итоги года) ═════════
function setupViewTabs() {
    const tabs = document.querySelectorAll(".view-tab");
    tabs.forEach(tab => {
        tab.addEventListener("click", () => switchView(tab.dataset.view));
    });
}

function switchView(view) {
    document.querySelectorAll(".view-tab").forEach(t => {
        t.classList.toggle("active", t.dataset.view === view);
    });
    const dashboardView = document.getElementById("view-dashboard");
    const yearView = document.getElementById("view-year");
    const periodToggle = document.getElementById("period-toggle");
    if (view === "year") {
        if (dashboardView) dashboardView.classList.add("hidden");
        if (yearView) yearView.classList.remove("hidden");
        if (periodToggle) periodToggle.classList.add("hidden");  // year — без period
        _loadYearReportIfNeeded();
    } else {
        if (dashboardView) dashboardView.classList.remove("hidden");
        if (yearView) yearView.classList.add("hidden");
        if (periodToggle) periodToggle.classList.remove("hidden");
    }
}

async function _loadYearReportIfNeeded() {
    if (state.yearReport || state.yearLoading) {
        if (state.yearReport) renderYearReport();
        return;
    }
    state.yearLoading = true;
    document.getElementById("year-loading").classList.remove("hidden");
    try {
        state.yearReport = await fetchJSON(`/api/dashboard/year/${state.currentStudentId}`);
        renderYearReport();
    } catch (e) {
        console.warn("Year report load failed", e);
        document.getElementById("year-loading").classList.add("hidden");
        document.getElementById("year-empty").classList.remove("hidden");
    } finally {
        state.yearLoading = false;
    }
}

// ═════════ KPI ROW (4 cards) ═════════
function renderKpis(kpis, summary) {
    const avgEl = document.getElementById("kpi-avg");
    const deltaEl = document.getElementById("kpi-delta");
    const countEl = document.getElementById("kpi-count");
    const topNameEl = document.getElementById("kpi-top-name");
    const topAvgEl = document.getElementById("kpi-top-avg");
    const worstNameEl = document.getElementById("kpi-worst-name");
    const worstAvgEl = document.getElementById("kpi-worst-avg");

    // Avg
    const avg = kpis.current_avg ?? summary.current_avg;
    if (avg == null) {
        avgEl.textContent = "—";
        avgEl.className = "kpi-value muted";
        deltaEl.classList.add("hidden");
    } else {
        avgEl.textContent = avg.toFixed(2);
        avgEl.className = "kpi-value " + gradeColorClass(avg);
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

    // Count
    countEl.textContent = kpis.total_grades ?? "—";

    // Top
    if (kpis.top_subject) {
        topNameEl.textContent = kpis.top_subject.name;
        topAvgEl.textContent = kpis.top_subject.avg.toFixed(2);
    } else {
        topNameEl.textContent = "—";
        topAvgEl.textContent = "";
    }

    // Worst
    if (kpis.worst_subject) {
        worstNameEl.textContent = kpis.worst_subject.name;
        worstAvgEl.textContent = kpis.worst_subject.avg.toFixed(2);
        worstAvgEl.className = "kpi-value-secondary " + gradeColorClass(kpis.worst_subject.avg);
    } else {
        worstNameEl.textContent = "—";
        worstAvgEl.textContent = "";
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
function renderQuartersBlock(quarters, bySubject, trendBySubject) {
    const wrap = document.getElementById("quarters-table-wrap");
    const empty = document.getElementById("quarters-empty");
    if (!wrap) return;

    // Карты: subject → current period stats (avg, count) и sparkline points
    const statsMap = new Map();
    (bySubject || []).forEach(s => statsMap.set(s.name, s));
    const trendMap = new Map();
    (trendBySubject || []).forEach(line => trendMap.set(line.subject, line.points));

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
        const stats = statsMap.get(q.subject);
        const sparkPoints = trendMap.get(q.subject);
        const trendSym = q.trend === 'up' ? '↑' : q.trend === 'down' ? '↓' : '→';
        const trendCls = q.trend === 'up' ? 'trend-up' : q.trend === 'down' ? 'trend-down' : 'trend-flat';
        const yearVal = q.year || '—';
        const yearCls = q.year_is_forecast ? 'qc-year-value forecast' : 'qc-year-value';
        const yearColorCls = q.year_value != null ? gradeColorClass(q.year_value) : '';

        // Quarter cells (если нет четвертных — empty placeholders)
        const quartersHtml = q._no_quarters
            ? `<div class="qc-quarters qc-quarters-empty">
                 <span class="muted small">${escapeHtml(t("quarters_no_data") || "Нет четвертных оценок")}</span>
               </div>`
            : `<div class="qc-quarters">
                 <div class="qc-q"><span class="qc-q-label">1ч</span>${cell(q.q1)}</div>
                 <div class="qc-q"><span class="qc-q-label">2ч</span>${cell(q.q2)}</div>
                 <div class="qc-q"><span class="qc-q-label">3ч</span>${cell(q.q3)}</div>
                 <div class="qc-q"><span class="qc-q-label">4ч</span>${cell(q.q4)}</div>
               </div>`;

        // Year column — only if quarter has it
        const yearHtml = q._no_quarters ? '' : `<div class="qc-year">
            <span class="qc-year-label">${escapeHtml(t("col_year") || "Год")}</span>
            <span class="${yearCls} ${yearColorCls}">${escapeHtml(yearVal)}</span>
        </div>`;

        // Footer: current-period stats + sparkline
        let footerHtml = '';
        if (stats || sparkPoints) {
            const sparkSvg = sparkPoints ? _sparklineSvg(sparkPoints, 80, 22) : '';
            const statsAvg = stats ? `<span class="qc-stat-avg ${gradeColorClass(stats.avg)}">${stats.avg.toFixed(2)}</span>` : '';
            const statsCount = stats ? `<span class="qc-stat-count">${stats.count} ${t("qc_stat_count_label") || "за период"}</span>` : '';
            footerHtml = `<div class="qc-footer">
                <div class="qc-footer-stats">${statsAvg}${statsCount}</div>
                <div class="qc-footer-spark ${trendCls}">${sparkSvg}</div>
            </div>`;
        }

        return `<div class="quarter-card" data-subject="${escapeHtml(q.subject)}">
            <div class="qc-header">
                <span class="qc-subject">${escapeHtml(q.subject)}</span>
                <span class="qc-trend ${trendCls}">${trendSym}</span>
            </div>
            <div class="qc-body">
                ${quartersHtml}
                ${yearHtml}
            </div>
            ${footerHtml}
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
const _GRADES_INITIAL_GROUPS = 7;  // показываем последние 7 дат

function _formatDateGroupLabel(dateStr) {
    if (!dateStr) return '?';
    const d = new Date(dateStr);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const diffDays = Math.floor((today - d) / (24 * 3600 * 1000));
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

function renderAllGrades(grades) {
    const list = document.getElementById("recent-list");
    const countBadge = document.getElementById("recent-count");
    const filter = document.getElementById("recent-filter");
    if (!list) return;

    countBadge.textContent = `(${grades.length})`;

    if (filter && filter.options.length <= 1) {
        const subjects = Array.from(new Set(grades.map(g => g.subject))).sort();
        subjects.forEach(s => {
            const opt = document.createElement("option");
            opt.value = s; opt.textContent = s;
            filter.appendChild(opt);
        });
        filter.addEventListener("change", () => {
            state._gradesGroupsShown = _GRADES_INITIAL_GROUPS;  // reset
            renderAllGrades(grades);
        });
    }

    const selected = filter ? filter.value : "";
    const filtered = selected ? grades.filter(g => g.subject === selected) : grades;

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

    const lastSeenStr = localStorage.getItem(LAST_SEEN_KEY(state.currentStudentId));
    const lastSeen = lastSeenStr ? new Date(lastSeenStr) : new Date(0);

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
        return `<div class="g-group">
            <div class="g-group-header">${escapeHtml(_formatDateGroupLabel(date))}</div>
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
    if (askBtn) {
        askBtn.onclick = () => _openBotChatWithQuestion(`Расскажи про ${subject}`);
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
            `${report.best_month.label} · ${report.best_month.avg.toFixed(2)}`;
    }
    if (report.worst_month) {
        document.getElementById("year-worst-month").textContent =
            `${report.worst_month.label} · ${report.worst_month.avg.toFixed(2)}`;
    }
}

function renderSubjectsList(subjects) {
    if (!subjects || subjects.length === 0) return `<p class="empty-hint">—</p>`;
    return subjects.map(s => `
        <div class="subject-row">
            <span class="subject-name">${escapeHtml(s.name)}</span>
            <span class="${gradeColorClass(s.avg)}">${s.avg.toFixed(2)}</span>
            <span class="subject-count muted">${s.count}</span>
        </div>
    `).join("");
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
    // Radical refactor: Share убран (use case слабый). Остались PDF
    // (полный proof-документ через bot) + AI deep-link.
    const pdfBtn = document.getElementById("btn-export-pdf");
    if (pdfBtn) pdfBtn.addEventListener("click", handleExportPdf);
    const aiBtn = document.getElementById("btn-open-ai");
    if (aiBtn) aiBtn.addEventListener("click", () => _openBotChatWithQuestion(""));
}

// Dashboard refactor: _buildShareText / handleShare удалены — Share use case
// слабый (см. user feedback). PDF (через bot) теперь основной way делиться
// данными.

// PDF: показываем модалку с выбором типа отчёта вместо instant-генерации
function handleExportPdf() {
    const modal = document.getElementById("pdf-modal");
    if (!modal) return;

    // Заполняем dropdown предметов
    const subjSel = document.getElementById("pdf-subject-select");
    if (subjSel && subjSel.options.length === 0) {
        const subjects = (state.dashboard?.by_subject || []).map(s => s.name);
        subjects.forEach(s => {
            const opt = document.createElement("option");
            opt.value = s; opt.textContent = s;
            subjSel.appendChild(opt);
        });
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
        _sendPdfRequest(type, subject);
    };

    modal.classList.remove("hidden");
}

async function _sendPdfRequest(reportType, subject) {
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
