/**
 * GradeSentinel WebApp — Grade Dashboard
 * Telegram Mini App with Chart.js visualizations
 */

const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

const initData = tg.initData;
const API_HEADERS = { "X-Telegram-Init-Data": initData };

let trendChart = null;
let subjectChart = null;
let allGrades = [];
let quarterGrades = [];
let currentDays = 7;
let currentSubject = "";

// ============ INIT ============

async function init() {
    try {
        const students = await fetchJSON("/api/students");
        if (!students || students.length === 0) {
            showError("No students linked to your account.");
            return;
        }

        // If multiple students, show selector
        if (students.length > 1) {
            const selector = document.getElementById("student-selector");
            const select = document.getElementById("student-select");
            selector.style.display = "block";
            students.forEach(s => {
                const opt = document.createElement("option");
                opt.value = s.id;
                opt.textContent = s.display_name || s.fio;
                select.appendChild(opt);
            });
            select.addEventListener("change", () => loadGrades(parseInt(select.value)));
        }

        // Setup period buttons
        document.querySelectorAll(".period-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                document.querySelectorAll(".period-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                currentDays = parseInt(btn.dataset.days);
                loadGrades(getCurrentStudentId(students));
            });
        });

        // Setup subject filter
        document.getElementById("subject-select").addEventListener("change", (e) => {
            currentSubject = e.target.value;
            loadGrades(getCurrentStudentId(students));
        });

        // Load first student
        await loadGrades(students[0].id);

        document.getElementById("loading").style.display = "none";
        document.getElementById("content").style.display = "block";
    } catch (e) {
        showError("Failed to load data: " + e.message);
    }
}

function getCurrentStudentId(students) {
    const select = document.getElementById("student-select");
    if (select.value) return parseInt(select.value);
    return students[0].id;
}

// ============ DATA LOADING ============

async function loadGrades(studentId) {
    try {
        let url = `/api/grades/${studentId}?days=${currentDays}`;
        if (currentSubject) url += `&subject=${encodeURIComponent(currentSubject)}`;

        allGrades = await fetchJSON(url);
        populateSubjectFilter();
        renderSummary();
        renderTrendChart();
        renderSubjectChart();
        renderTable();

        // Load quarter grades (once per student switch)
        if (!currentSubject) {
            try {
                quarterGrades = await fetchJSON(`/api/quarters/${studentId}`);
                renderQuarters();
            } catch (e) {
                quarterGrades = [];
            }
        }
    } catch (e) {
        showError("Failed to load grades: " + e.message);
    }
}

function populateSubjectFilter() {
    const select = document.getElementById("subject-select");
    const currentVal = select.value;
    const subjects = [...new Set(allGrades.map(g => g.subject))].sort();

    // Only repopulate if subjects changed
    const existingOpts = [...select.options].slice(1).map(o => o.value);
    if (JSON.stringify(subjects) === JSON.stringify(existingOpts)) return;

    // Keep "All subjects" option, clear rest
    while (select.options.length > 1) select.remove(1);
    subjects.forEach(s => {
        const opt = document.createElement("option");
        opt.value = s;
        opt.textContent = s;
        select.appendChild(opt);
    });
    select.value = currentVal;
}

async function fetchJSON(url) {
    const res = await fetch(url, { headers: API_HEADERS });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

// ============ SUMMARY CARDS ============

function renderSummary() {
    const numeric = allGrades.filter(g => g.grade_value !== null);

    // Average
    const avg = numeric.length > 0
        ? (numeric.reduce((s, g) => s + g.grade_value, 0) / numeric.length).toFixed(1)
        : "—";
    document.getElementById("avg-grade").textContent = avg;

    // Total
    document.getElementById("total-grades").textContent = allGrades.length;

    // Best subject
    const bySubject = {};
    numeric.forEach(g => {
        if (!bySubject[g.subject]) bySubject[g.subject] = [];
        bySubject[g.subject].push(g.grade_value);
    });
    let best = "—";
    let bestAvg = 0;
    for (const [subj, vals] of Object.entries(bySubject)) {
        const a = vals.reduce((s, v) => s + v, 0) / vals.length;
        if (a > bestAvg) { bestAvg = a; best = subj; }
    }
    const bestEl = document.getElementById("best-subject");
    bestEl.textContent = best.length > 10 ? best.substring(0, 9) + "..." : best;
    bestEl.title = best;
}

// ============ TREND CHART ============

function renderTrendChart() {
    const ctx = document.getElementById("trendChart").getContext("2d");
    const numeric = allGrades
        .filter(g => g.grade_value !== null)
        .sort((a, b) => a.date_added.localeCompare(b.date_added));

    const labels = numeric.map(g => formatDate(g.date_added));
    const values = numeric.map(g => g.grade_value);

    if (trendChart) trendChart.destroy();

    trendChart = new Chart(ctx, {
        type: "line",
        data: {
            labels,
            datasets: [{
                label: "Grade",
                data: values,
                borderColor: getComputedStyle(document.documentElement)
                    .getPropertyValue("--tg-theme-button-color") || "#2481cc",
                backgroundColor: "rgba(36, 129, 204, 0.1)",
                fill: true,
                tension: 0.3,
                pointRadius: 3,
                pointBackgroundColor: getComputedStyle(document.documentElement)
                    .getPropertyValue("--tg-theme-button-color") || "#2481cc",
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { min: 1, max: 5, ticks: { stepSize: 1 } },
                x: { display: true, ticks: { maxRotation: 45, font: { size: 10 } } }
            },
            plugins: { legend: { display: false } }
        }
    });
}

// ============ SUBJECT AVERAGES CHART ============

function renderSubjectChart() {
    const ctx = document.getElementById("subjectChart").getContext("2d");
    const numeric = allGrades.filter(g => g.grade_value !== null);

    const bySubject = {};
    numeric.forEach(g => {
        if (!bySubject[g.subject]) bySubject[g.subject] = [];
        bySubject[g.subject].push(g.grade_value);
    });

    const subjects = Object.keys(bySubject);
    const averages = subjects.map(s => {
        const vals = bySubject[s];
        return +(vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(1);
    });

    // Color based on average
    const colors = averages.map(a => {
        if (a >= 4.5) return "#28a745";
        if (a >= 3.5) return "#2481cc";
        if (a >= 2.5) return "#ffc107";
        return "#dc3545";
    });

    if (subjectChart) subjectChart.destroy();

    subjectChart = new Chart(ctx, {
        type: "bar",
        data: {
            labels: subjects.map(s => s.length > 12 ? s.substring(0, 11) + "..." : s),
            datasets: [{
                data: averages,
                backgroundColor: colors,
                borderRadius: 6,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: "y",
            scales: {
                x: { min: 0, max: 5, ticks: { stepSize: 1 } }
            },
            plugins: { legend: { display: false } }
        }
    });
}

// ============ GRADES TABLE ============

function renderTable() {
    const container = document.getElementById("grades-table");
    // Show most recent first (already sorted DESC from API)
    const recent = allGrades.slice(0, 20);

    if (recent.length === 0) {
        container.innerHTML = '<p style="text-align:center;color:var(--tg-hint)">No grades for this period</p>';
        return;
    }

    container.innerHTML = recent.map(g => {
        const gradeClass = g.grade_value !== null
            ? `grade-${Math.round(g.grade_value)}`
            : "grade-text";
        const displayValue = g.raw_text || "—";
        return `
            <div class="grade-row">
                <span class="grade-subject">${escapeHtml(g.subject)}</span>
                <span class="grade-date">${formatDate(g.date_added)}</span>
                <span class="grade-value ${gradeClass}">${escapeHtml(displayValue)}</span>
            </div>
        `;
    }).join("");
}

// ============ QUARTER GRADES ============

function renderQuarters() {
    const section = document.getElementById("quarters-section");
    const container = document.getElementById("quarters-table");

    if (!quarterGrades || quarterGrades.length === 0) {
        section.style.display = "none";
        return;
    }

    section.style.display = "block";
    const qNames = { 1: "1ч", 2: "2ч", 3: "3ч", 4: "4ч", 5: "Год" };

    // Group by subject
    const bySubject = {};
    quarterGrades.forEach(q => {
        if (!bySubject[q.subject]) bySubject[q.subject] = {};
        bySubject[q.subject][q.quarter] = q;
    });

    let html = '<div class="quarter-grid"><div class="qr-header"><span></span>';
    for (let i = 1; i <= 5; i++) html += `<span>${qNames[i]}</span>`;
    html += '</div>';

    for (const [subject, quarters] of Object.entries(bySubject)) {
        html += `<div class="qr-row"><span class="qr-subject">${escapeHtml(subject)}</span>`;
        for (let i = 1; i <= 5; i++) {
            const q = quarters[i];
            if (q && q.raw_text) {
                const cls = q.grade_value ? `grade-${Math.round(q.grade_value)}` : "grade-text";
                html += `<span class="${cls}">${escapeHtml(q.raw_text)}</span>`;
            } else {
                html += `<span>—</span>`;
            }
        }
        html += '</div>';
    }
    html += '</div>';
    container.innerHTML = html;
}

// ============ HELPERS ============

function formatDate(dateStr) {
    if (!dateStr) return "";
    const d = new Date(dateStr);
    return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function showError(msg) {
    document.getElementById("loading").style.display = "none";
    document.getElementById("content").style.display = "none";
    document.getElementById("error").style.display = "block";
    document.getElementById("error-text").textContent = msg;
}

// ============ START ============
init();
