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
        const res = await fetch(`/static/locales/${lang}.json`, { cache: "force-cache" });
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
    renderQuartersBlock(d.quarters_with_forecast || []);
    renderTrendBySubject(d.trend_by_subject || []);
    renderSubjectsTable(d.by_subject || []);
    renderAllGrades(d.recent_grades || []);

    // Mark студента как просмотренного — для подсветки "новое" в следующий заход
    localStorage.setItem(LAST_SEEN_KEY(state.currentStudentId), new Date().toISOString());

    // Year report: lazy + auto-expand в конце учебного года (апрель-июнь)
    setupYearReportLazy();
    _maybeAutoExpandYearReport();
}

function _maybeAutoExpandYearReport() {
    const month = new Date().getMonth() + 1;  // 1-12
    if (month >= 4 && month <= 6) {
        const section = document.getElementById("year-section");
        if (section && !section.classList.contains("open")) {
            toggleSection(section);
        }
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

// Multi-line trend BY SUBJECT (заменил старый trend by day — был шумом).
// Каждая линия = один предмет, точки по неделям. Фильтр-чекбоксы под графиком.
const _TREND_PALETTE = [
    "#6366F1", "#10B981", "#F59E0B", "#EF4444", "#0EA5E9",
    "#8B5CF6", "#EC4899", "#14B8A6",
];

function renderTrendBySubject(trendBySubject) {
    const ctx = document.getElementById("trendChart")?.getContext("2d");
    const emptyHint = document.getElementById("trend-empty");
    const filtersEl = document.getElementById("trend-filters");
    if (!ctx) return;

    if (state.trendChart) { state.trendChart.destroy(); state.trendChart = null; }

    if (!trendBySubject || trendBySubject.length === 0) {
        emptyHint.classList.remove("hidden");
        ctx.canvas.classList.add("hidden");
        if (filtersEl) filtersEl.innerHTML = "";
        return;
    }
    emptyHint.classList.add("hidden");
    ctx.canvas.classList.remove("hidden");

    // Собираем union всех week-точек чтобы Chart.js мог их align'нуть
    const weekSet = new Set();
    trendBySubject.forEach(line => line.points.forEach(p => weekSet.add(p.week)));
    const weeks = Array.from(weekSet).sort();
    const labels = weeks.map(w => formatDateShort(w));

    state._trendSubjectsState = state._trendSubjectsState || {};
    const datasets = trendBySubject.map((line, i) => {
        const color = _TREND_PALETTE[i % _TREND_PALETTE.length];
        // По умолчанию все видны; per-subject hide через filter buttons
        const hidden = state._trendSubjectsState[line.subject] === false;
        const valuesByWeek = Object.fromEntries(line.points.map(p => [p.week, p.avg]));
        return {
            label: line.subject,
            data: weeks.map(w => valuesByWeek[w] ?? null),
            borderColor: color,
            backgroundColor: hexToRgba(color, 0.1),
            tension: 0.25,
            spanGaps: true,
            pointRadius: 3,
            hidden,
        };
    });

    state.trendChart = new Chart(ctx, {
        type: "line",
        data: { labels, datasets },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: { display: false },  // у нас свои filter-кнопки
                tooltip: {
                    callbacks: {
                        label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(2) ?? "—"}`,
                    },
                },
            },
            scales: {
                y: { min: 1, max: 5, ticks: { stepSize: 1 } },
                x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 6 } },
            },
        },
    });

    // Filter кнопки — toggle visibility per subject
    if (filtersEl) {
        filtersEl.innerHTML = trendBySubject.map((line, i) => {
            const color = _TREND_PALETTE[i % _TREND_PALETTE.length];
            const isOff = state._trendSubjectsState[line.subject] === false;
            return `<button class="trend-filter-btn ${isOff ? "off" : ""}"
                            data-subject="${escapeHtml(line.subject)}"
                            style="--dot:${color}">
                        <span class="trend-dot"></span>${escapeHtml(line.subject)}
                    </button>`;
        }).join("");
        filtersEl.querySelectorAll(".trend-filter-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                const subj = btn.dataset.subject;
                const wasOff = state._trendSubjectsState[subj] === false;
                state._trendSubjectsState[subj] = wasOff;  // toggle
                renderTrendBySubject(trendBySubject);
            });
        });
    }

}

// ═════════ QUARTERS BLOCK (primary) ═════════
function renderQuartersBlock(quarters) {
    const wrap = document.getElementById("quarters-table-wrap");
    const empty = document.getElementById("quarters-empty");
    if (!wrap) return;

    if (!quarters || quarters.length === 0) {
        wrap.innerHTML = "";
        if (empty) empty.classList.remove("hidden");
        return;
    }
    if (empty) empty.classList.add("hidden");

    const headers = [
        t("col_subject") || "Предмет",
        "1ч", "2ч", "3ч", "4ч",
        t("col_year") || "Год",
        t("col_trend") || "Тренд",
    ];

    const rows = quarters.map(q => {
        const cell = (val, isForecast) => {
            if (val == null || val === '') return `<td class="qr-cell muted">—</td>`;
            const cls = isForecast ? "qr-cell qr-forecast" : "qr-cell";
            return `<td class="${cls}">${escapeHtml(String(val))}</td>`;
        };
        const trendSym = q.trend === 'up' ? '↑' : q.trend === 'down' ? '↓' : '→';
        const trendCls = q.trend === 'up' ? 'trend-up' : q.trend === 'down' ? 'trend-down' : 'trend-flat';
        return `<tr class="qr-row" data-subject="${escapeHtml(q.subject)}">
            <td class="qr-subject">${escapeHtml(q.subject)}</td>
            ${cell(q.q1)}${cell(q.q2)}${cell(q.q3)}${cell(q.q4)}
            ${cell(q.year, q.year_is_forecast)}
            <td class="qr-trend ${trendCls}">${trendSym}</td>
        </tr>`;
    }).join("");

    wrap.innerHTML = `<table class="qr-table">
        <thead><tr>${headers.map(h => `<th>${escapeHtml(h)}</th>`).join("")}</tr></thead>
        <tbody>${rows}</tbody>
    </table>
    <p class="qr-note muted">${escapeHtml(t("quarters_forecast_note") || "")}</p>`;

    // Click row → drill-down
    wrap.querySelectorAll(".qr-row").forEach(tr => {
        tr.addEventListener("click", () => openDrilldown(tr.dataset.subject));
    });
}

// ═════════ ALL SUBJECTS TABLE (sortable + clickable) ═════════
function renderSubjectsTable(subjects) {
    const wrap = document.getElementById("subjects-table-wrap");
    if (!wrap) return;
    if (!subjects || subjects.length === 0) {
        wrap.innerHTML = `<p class="empty-hint">${t("hero_no_grades")}</p>`;
        return;
    }

    state._subjectsSort = state._subjectsSort || { key: 'avg', dir: 'desc' };
    const sortKey = state._subjectsSort.key;
    const sortDir = state._subjectsSort.dir;
    const sorted = subjects.slice().sort((a, b) => {
        let av = a[sortKey], bv = b[sortKey];
        if (typeof av === 'string') av = av.toLocaleLowerCase();
        if (typeof bv === 'string') bv = bv.toLocaleLowerCase();
        const cmp = av < bv ? -1 : av > bv ? 1 : 0;
        return sortDir === 'desc' ? -cmp : cmp;
    });

    const headers = [
        { key: 'name', label: t("col_subject") || "Предмет" },
        { key: 'avg', label: t("col_avg") || "Средний" },
        { key: 'count', label: t("col_count") || "Кол-во" },
        { key: 'last_grade', label: t("col_last") || "Последняя", noSort: true },
        { key: 'trend', label: t("col_trend") || "Тренд", noSort: true },
    ];

    const ths = headers.map(h => {
        const arrow = (h.key === sortKey) ? (sortDir === 'desc' ? ' ↓' : ' ↑') : '';
        return `<th data-sort="${h.noSort ? '' : h.key}" class="${h.noSort ? '' : 'sortable'}">${escapeHtml(h.label)}${arrow}</th>`;
    }).join("");

    const rows = sorted.map(s => {
        const avgCls = gradeColorClass(s.avg);
        const trendSym = s.trend === 'up' ? '↑' : s.trend === 'down' ? '↓' : '→';
        const trendCls = s.trend === 'up' ? 'trend-up' : s.trend === 'down' ? 'trend-down' : 'trend-flat';
        const lastDate = s.last_date ? `<span class="muted small">${escapeHtml(s.last_date.slice(5))}</span>` : '';
        return `<tr class="subj-table-row" data-subject="${escapeHtml(s.name)}">
            <td>${escapeHtml(s.name)}</td>
            <td class="${avgCls}">${s.avg.toFixed(2)}</td>
            <td>${s.count}</td>
            <td>${escapeHtml(s.last_grade || '—')} ${lastDate}</td>
            <td class="${trendCls}">${trendSym}</td>
        </tr>`;
    }).join("");

    wrap.innerHTML = `<table class="subj-table">
        <thead><tr>${ths}</tr></thead>
        <tbody>${rows}</tbody>
    </table>`;

    // Sort handlers
    wrap.querySelectorAll("th.sortable").forEach(th => {
        th.addEventListener("click", () => {
            const k = th.dataset.sort;
            if (state._subjectsSort.key === k) {
                state._subjectsSort.dir = state._subjectsSort.dir === 'desc' ? 'asc' : 'desc';
            } else {
                state._subjectsSort.key = k;
                state._subjectsSort.dir = 'desc';
            }
            renderSubjectsTable(subjects);
        });
    });
    // Drill-down on row click
    wrap.querySelectorAll(".subj-table-row").forEach(tr => {
        tr.addEventListener("click", () => openDrilldown(tr.dataset.subject));
    });
}

// ═════════ ALL GRADES (with subject filter) ═════════
function renderAllGrades(grades) {
    const list = document.getElementById("recent-list");
    const countBadge = document.getElementById("recent-count");
    const filter = document.getElementById("recent-filter");
    if (!list) return;

    countBadge.textContent = `(${grades.length})`;

    // Populate filter dropdown
    if (filter && filter.options.length <= 1) {
        const subjects = Array.from(new Set(grades.map(g => g.subject))).sort();
        subjects.forEach(s => {
            const opt = document.createElement("option");
            opt.value = s; opt.textContent = s;
            filter.appendChild(opt);
        });
        filter.addEventListener("change", () => renderAllGrades(grades));
    }

    const selected = filter ? filter.value : "";
    const filtered = selected ? grades.filter(g => g.subject === selected) : grades;

    if (filtered.length === 0) {
        list.innerHTML = `<p class="empty-hint">${t("hero_no_grades")}</p>`;
        return;
    }

    const lastSeenStr = localStorage.getItem(LAST_SEEN_KEY(state.currentStudentId));
    const lastSeen = lastSeenStr ? new Date(lastSeenStr) : new Date(0);

    list.innerHTML = filtered.map(g => {
        const isNew = g.date_added && new Date(g.date_added) > lastSeen;
        const colorClass = g.grade_value !== null ? gradeColorClass(g.grade_value) : "grade-text";
        const value = g.raw_text || (g.grade_value !== null ? g.grade_value : "—");
        const newBadge = isNew ? `<span class="badge-new">${t("badge_new") || "новое"}</span>` : "";
        const date = g.grade_date || (g.date_added ? g.date_added.slice(0, 10) : '');
        return `<div class="recent-row ${isNew ? "is-new" : ""}">
            <span class="recent-date">${escapeHtml(date)}</span>
            <span class="recent-subject">${escapeHtml(g.subject)}${newBadge}</span>
            <span class="recent-grade ${colorClass}">${escapeHtml(String(value))}</span>
        </div>`;
    }).join("");
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
    // Открыть бот-чат с pre-filled вопросом. Telegram WebApp:
    // tg.close() + bot отправит сообщение с suggestion?
    // Простейший вариант: t.me/<bot_username>?start=ask_<encoded_question>
    // Но bot_username нам не передан в frontend.
    // MVP: просто tg.close() — юзер увидит бота, может задать вопрос сам.
    const tg = window.Telegram && window.Telegram.WebApp;
    if (tg) {
        if (tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
        if (tg.close) tg.close();
    }
}

// ============ YEAR REPORT — LAZY ============

function setupYearReportLazy() {
    const section = document.getElementById("year-section");
    if (!section) return;
    section.addEventListener("toggle:open", async () => {
        if (state.yearReport !== null || state.yearLoading) return;
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
    }, { once: true });
}

function renderYearReport() {
    const report = state.yearReport;
    document.getElementById("year-loading").classList.add("hidden");

    if (!report || report.numeric_count < 1) {
        document.getElementById("year-empty").classList.remove("hidden");
        return;
    }

    document.getElementById("year-content").classList.remove("hidden");

    document.getElementById("year-avg").textContent = report.year_avg !== null ? report.year_avg.toFixed(2) : "—";
    document.getElementById("year-total-grades").textContent = report.numeric_count;

    const growthEl = document.getElementById("year-growth");
    if (report.growth !== null && report.growth !== undefined) {
        const sign = report.growth > 0 ? "+" : "";
        growthEl.textContent = `${sign}${report.growth}`;
        growthEl.className = "year-stat-value " + (report.growth > 0 ? "grade-good" : (report.growth < 0 ? "grade-warn" : ""));
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

    // Период учебного года
    if (report.school_year_start) {
        const startYear = report.school_year_start.slice(0, 4);
        document.getElementById("year-period").textContent = ` · ${startYear}—${parseInt(startYear) + 1}`;
    }

    // Top/problem subjects
    const topListEl = document.getElementById("year-top-subjects");
    topListEl.innerHTML = renderSubjectsList(report.top_subjects);
    if (report.problem_subjects && report.problem_subjects.length) {
        document.getElementById("year-problem-wrap").classList.remove("hidden");
        document.getElementById("year-problem-subjects").innerHTML = renderSubjectsList(report.problem_subjects);
    }

    // AI insight
    if (report.ai_insight) {
        document.getElementById("year-insight").textContent = report.ai_insight;
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

async function handleExportPdf() {
    // Стратегия: POST /pdf/send → backend генерит PDF и шлёт его как
    // документ в чат с ботом через Bot API. Юзер видит файл как обычное
    // сообщение в Telegram, может сохранять/пересылать стандартно.
    //
    // Раньше пробовали blob: download через <a download> — но Telegram
    // WebView показывает диалог «Открыть blob://?» вместо скачивания.
    // Send-to-bot работает на всех Telegram клиентах (desktop, mobile).
    const studentId = state.currentStudentId;
    const days = state.currentDays || 30;

    const pdfBtn = document.getElementById("btn-export-pdf");
    const originalText = pdfBtn ? pdfBtn.textContent : "";
    if (pdfBtn) {
        pdfBtn.disabled = true;
        pdfBtn.textContent = "⏳ " + (t("action_export_loading") || "PDF…");
    }

    try {
        const res = await fetch(
            `/api/dashboard/${studentId}/pdf/send?days=${days}`,
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
