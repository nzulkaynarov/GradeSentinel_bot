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

        // Dashboard refresh: action bar (Поделиться + PDF)
        setupActionBar();

        // Показать контент, скрыть скелетон
        hide("skeleton");
        show("content");
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
    const d = state.dashboard;
    if (!d) return;

    renderHero(d.summary);
    renderTrend(d.trend_by_day);
    renderProblems(d.summary.problem_subjects);
    renderTop(d.summary.top_subjects);
    renderSubjects(d.by_subject);
    renderRecent(d.recent_grades);

    // Mark студента как просмотренного — для подсветки "новое" в следующий заход
    localStorage.setItem(LAST_SEEN_KEY(state.currentStudentId), new Date().toISOString());

    // Quarters: lazy при раскрытии секции
    setupQuartersLazy();
    // Year report: lazy при раскрытии секции
    setupYearReportLazy();
    // Dashboard refresh: chat-section и AI-обзор удалены. Action bar
    // (Поделиться + PDF) монтируется отдельно при init (см. setupActionBar).
}

function renderHero(summary) {
    const avgEl = document.getElementById("hero-avg");
    const deltaEl = document.getElementById("hero-delta");
    const periodEl = document.getElementById("hero-period");
    const statusEl = document.getElementById("hero-status");

    if (summary.current_avg === null) {
        avgEl.textContent = "—";
        avgEl.classList.add("muted");
        deltaEl.classList.add("hidden");
        periodEl.textContent = t("hero_no_grades");
        statusEl.textContent = t("hero_no_grades_hint");
        statusEl.className = "hero-status";
        return;
    }

    avgEl.textContent = summary.current_avg.toFixed(1);
    avgEl.classList.remove("muted");

    // Цвет hero по среднему
    avgEl.className = "hero-avg " + gradeColorClass(summary.current_avg);

    // Дельта
    if (summary.delta !== null && Math.abs(summary.delta) >= 0.05) {
        deltaEl.classList.remove("hidden");
        deltaEl.classList.toggle("delta-up", summary.delta > 0);
        deltaEl.classList.toggle("delta-down", summary.delta < 0);
        deltaEl.querySelector(".delta-arrow").textContent = summary.delta > 0 ? "↑" : "↓";
        deltaEl.querySelector(".delta-value").textContent =
            `${summary.delta > 0 ? "+" : ""}${summary.delta.toFixed(1)}`;
    } else {
        deltaEl.classList.add("hidden");
    }

    periodEl.textContent = formatPeriod(summary.period_start, summary.period_end);

    // Status строка
    statusEl.textContent = t(`status_${summary.status}`);
    statusEl.className = "hero-status status-" + summary.status;
}

// Dashboard refresh: SUGGESTED_PROMPTS, renderInsight, _openChatWithPrompt
// удалены. AI-фичи (insight + suggested prompts + chat) теперь только в
// боте — webapp дашборд только данные + Share/PDF.

function renderTrend(trendData) {
    const ctx = document.getElementById("trendChart")?.getContext("2d");
    const emptyHint = document.getElementById("trend-empty");
    const trendSection = document.getElementById("trend-section");
    if (!ctx) return;

    if (state.trendChart) {
        state.trendChart.destroy();
        state.trendChart = null;
    }

    if (!trendData || trendData.length < 2) {
        emptyHint.classList.remove("hidden");
        ctx.canvas.classList.add("hidden");
        return;
    }
    emptyHint.classList.add("hidden");
    ctx.canvas.classList.remove("hidden");

    const themeColor = getThemeColor("--tg-theme-button-color", "#2481cc");
    const labels = trendData.map(d => formatDateShort(d.date));
    const values = trendData.map(d => d.avg);

    state.trendChart = new Chart(ctx, {
        type: "line",
        data: {
            labels,
            datasets: [{
                data: values,
                borderColor: themeColor,
                backgroundColor: hexToRgba(themeColor, 0.12),
                fill: true,
                tension: 0.35,
                pointRadius: 4,
                pointHoverRadius: 6,
                pointBackgroundColor: themeColor,
                pointBorderColor: "#fff",
                pointBorderWidth: 2,
                borderWidth: 2.5,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 600, easing: "easeOutQuart" },
            scales: {
                y: { min: 1, max: 5, ticks: { stepSize: 1, color: getThemeColor("--tg-theme-hint-color", "#aaa") }, grid: { color: hexToRgba(getThemeColor("--tg-theme-hint-color", "#aaa"), 0.1) } },
                x: { ticks: { color: getThemeColor("--tg-theme-hint-color", "#aaa"), maxRotation: 0, autoSkip: true, maxTicksLimit: 7 }, grid: { display: false } }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: getThemeColor("--tg-theme-bg-color", "#000"),
                    titleColor: getThemeColor("--tg-theme-text-color", "#fff"),
                    bodyColor: getThemeColor("--tg-theme-text-color", "#fff"),
                    borderColor: themeColor,
                    borderWidth: 1,
                }
            }
        }
    });
}

function renderProblems(problems) {
    const section = document.getElementById("problems-section");
    const list = document.getElementById("problems-list");
    if (!problems || problems.length === 0) {
        section.classList.add("hidden");
        return;
    }
    section.classList.remove("hidden");
    list.innerHTML = problems.map(s => subjectRow(s, "warning")).join("");
}

function renderTop(top) {
    const section = document.getElementById("top-section");
    const list = document.getElementById("top-list");
    if (!top || top.length === 0) {
        section.classList.add("hidden");
        return;
    }
    section.classList.remove("hidden");
    list.innerHTML = top.map(s => subjectRow(s, "success")).join("");
}

function renderSubjects(subjects) {
    const list = document.getElementById("subjects-list");
    if (!subjects || subjects.length === 0) {
        list.innerHTML = `<p class="empty-hint">${t("hero_no_grades")}</p>`;
        return;
    }
    list.innerHTML = subjects.map(s => subjectRow(s, "neutral")).join("");
}

function subjectRow(subj, _accent) {
    const colorClass = gradeColorClass(subj.avg);
    const deltaHtml = subj.delta !== null && subj.delta !== undefined && Math.abs(subj.delta) >= 0.1
        ? `<span class="subj-delta ${subj.delta > 0 ? "up" : "down"}">${subj.delta > 0 ? "↑" : "↓"} ${Math.abs(subj.delta).toFixed(1)}</span>`
        : "";
    return `
        <div class="subj-row">
            <span class="subj-name">${escapeHtml(subj.name)}</span>
            <span class="subj-meta">
                ${deltaHtml}
                <span class="subj-avg ${colorClass}">${subj.avg.toFixed(1)}</span>
            </span>
        </div>
    `;
}

function renderRecent(grades) {
    const list = document.getElementById("recent-list");
    const countBadge = document.getElementById("recent-count");
    countBadge.textContent = `(${grades.length})`;

    if (!grades || grades.length === 0) {
        list.innerHTML = `<p class="empty-hint">${t("hero_no_grades")}</p>`;
        return;
    }

    const lastSeenStr = localStorage.getItem(LAST_SEEN_KEY(state.currentStudentId));
    const lastSeen = lastSeenStr ? new Date(lastSeenStr) : new Date(0);

    list.innerHTML = grades.map(g => {
        const isNew = new Date(g.date_added) > lastSeen;
        const colorClass = g.grade_value !== null ? gradeColorClass(g.grade_value) : "grade-text";
        const value = g.raw_text || (g.grade_value !== null ? g.grade_value : "—");
        const newBadge = isNew ? `<span class="badge-new">${t("badge_new")}</span>` : "";
        return `
            <div class="recent-row ${isNew ? "is-new" : ""}">
                <span class="recent-date">${formatDateShort(g.date_added)}</span>
                <span class="recent-subject">${escapeHtml(g.subject)}${newBadge}</span>
                <span class="recent-grade ${colorClass}">${escapeHtml(String(value))}</span>
            </div>
        `;
    }).join("");
}

// ============ QUARTERS — LAZY ============

function setupQuartersLazy() {
    const section = document.getElementById("quarters-section");
    const body = section.querySelector(".card-body");
    const toggleBtn = section.querySelector(".toggle-btn");

    // Если уже загружено и UI открыт — рендерим. Иначе — ждём первого раскрытия.
    section.addEventListener("toggle:open", async () => {
        if (state.quarters !== null || state.quartersLoading) return;
        state.quartersLoading = true;
        try {
            state.quarters = await fetchJSON(`/api/quarters/${state.currentStudentId}`);
            renderQuartersTable();
        } catch (e) {
            console.warn("Quarters load failed", e);
            body.innerHTML = `<p class="empty-hint">${t("error_generic")}</p>`;
        } finally {
            state.quartersLoading = false;
        }
    }, { once: true });
}

function renderQuartersTable() {
    const container = document.getElementById("quarters-table");
    const quarters = state.quarters;
    if (!quarters || quarters.length === 0) {
        container.innerHTML = `<p class="empty-hint">${t("hero_no_grades")}</p>`;
        return;
    }

    const qNames = {
        1: t("quarter_1"), 2: t("quarter_2"), 3: t("quarter_3"),
        4: t("quarter_4"), 5: t("quarter_year"),
    };

    const bySubject = {};
    quarters.forEach(q => {
        if (!bySubject[q.subject]) bySubject[q.subject] = {};
        bySubject[q.subject][q.quarter] = q;
    });

    let html = `<div class="quarter-grid"><div class="qr-header"><span></span>`;
    for (let i = 1; i <= 5; i++) html += `<span>${qNames[i]}</span>`;
    html += `</div>`;

    for (const [subject, qmap] of Object.entries(bySubject)) {
        html += `<div class="qr-row"><span class="qr-subject">${escapeHtml(subject)}</span>`;
        for (let i = 1; i <= 5; i++) {
            const q = qmap[i];
            if (q && q.raw_text) {
                const cls = q.grade_value !== null ? gradeColorClass(q.grade_value) : "grade-text";
                html += `<span class="${cls}">${escapeHtml(q.raw_text)}</span>`;
            } else {
                html += `<span class="muted">—</span>`;
            }
        }
        html += `</div>`;
    }
    html += `</div>`;
    container.innerHTML = html;
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
    const shareBtn = document.getElementById("btn-share");
    const pdfBtn = document.getElementById("btn-export-pdf");
    if (shareBtn) shareBtn.addEventListener("click", handleShare);
    if (pdfBtn) pdfBtn.addEventListener("click", handleExportPdf);
}

function _buildShareText() {
    // Краткая текстовая выжимка дашборда — для Telegram share. Собираем
    // только из state.dashboard (уже загружено).
    const d = state.dashboard;
    if (!d) return "";

    const student = state.students.find(s => s.id === state.currentStudentId);
    const name = student ? (student.display_name || student.fio) : "Ученик";
    const summary = d.summary || {};
    const lines = [];

    lines.push(`📊 ${name}`);
    if (summary.current_avg != null) {
        const delta = summary.delta;
        let deltaStr = "";
        if (delta != null && Math.abs(delta) >= 0.05) {
            deltaStr = ` (${delta > 0 ? "+" : ""}${delta.toFixed(1)})`;
        }
        lines.push(`${t("hero_average")}: ${summary.current_avg.toFixed(1)}${deltaStr}`);
    }
    const tops = (summary.top_subjects || []).slice(0, 2).map(s => s.name).join(", ");
    if (tops) lines.push(`✨ ${t("section_top")}: ${tops}`);
    const problems = (summary.problem_subjects || []).slice(0, 2).map(s => s.name).join(", ");
    if (problems) lines.push(`⚠️ ${t("section_problems")}: ${problems}`);

    lines.push("");
    lines.push("— GradeSentinel");
    return lines.join("\n");
}

function handleShare() {
    const text = _buildShareText();
    if (!text) return;
    // Telegram t.me/share/url — стандартный share-link, открывает диалог
    // выбора чата. url оставляем пустым (нет публичного permalink),
    // text передаём через query.
    const shareUrl = `https://t.me/share/url?url=&text=${encodeURIComponent(text)}`;
    const tg = window.Telegram && window.Telegram.WebApp;
    if (tg && typeof tg.openTelegramLink === "function") {
        tg.openTelegramLink(shareUrl);
        if (tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
    } else {
        window.open(shareUrl, "_blank");
    }
}

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
