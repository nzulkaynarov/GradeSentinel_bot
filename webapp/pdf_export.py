"""Export dashboard data as PDF (NAV-следующий: dashboard refresh).

Используется reportlab + Platypus (высокоуровневое API: Paragraph, Table,
Spacer). Шрифт для кириллицы — DejaVuSans (есть в Ubuntu base пакете
fonts-dejavu, fallback на Helvetica если ничего не нашлось — кириллица
отрендерится как квадратики, но crash не будет).

Контракт: build_dashboard_pdf(student_name, summary, trend, by_subject,
recent, lang, period_label) → bytes. Caller обёртывает в Flask Response.
"""
import io
import logging
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
    KeepTogether,
)

logger = logging.getLogger(__name__)

# Кандидаты на шрифт с кириллической поддержкой. Ubuntu base ставит DejaVu
# через fonts-dejavu (пакет в зависимостях reportlab под Debian не входит,
# но обычно установлен). Mac — Library/Fonts.
_FONT_CANDIDATES = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans.ttf',
    '/Library/Fonts/Arial Unicode.ttf',
    '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
]
_FONT_BOLD_CANDIDATES = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',
]

_FONT_REGISTERED = False
_FONT_NAME = 'Helvetica'
_FONT_BOLD = 'Helvetica-Bold'


def _ensure_font():
    """Лениво регистрирует TTF шрифт с кириллицей. Безопасно деградирует
    на Helvetica если DejaVu не найден (кириллица станет квадратиками, но
    PDF сгенерится)."""
    global _FONT_REGISTERED, _FONT_NAME, _FONT_BOLD
    if _FONT_REGISTERED:
        return

    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont('DashboardFont', path))
                _FONT_NAME = 'DashboardFont'
                logger.info(f"PDF font registered: {path}")
                break
            except Exception as e:
                logger.warning(f"Failed to register {path}: {e}")

    for path in _FONT_BOLD_CANDIDATES:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont('DashboardFontBold', path))
                _FONT_BOLD = 'DashboardFontBold'
                break
            except Exception as e:
                logger.warning(f"Failed to register bold {path}: {e}")

    if _FONT_NAME == 'Helvetica':
        logger.warning("PDF: DejaVu не найден, кириллица может рендериться квадратиками.")

    _FONT_REGISTERED = True


def _styles():
    """Готовит набор Paragraph-стилей с правильным шрифтом."""
    _ensure_font()
    base = getSampleStyleSheet()
    return {
        'title': ParagraphStyle(
            'title', parent=base['Title'],
            fontName=_FONT_BOLD, fontSize=18, leading=22,
            spaceAfter=8, textColor=colors.HexColor('#0F172A'),
        ),
        'subtitle': ParagraphStyle(
            'subtitle', parent=base['Normal'],
            fontName=_FONT_NAME, fontSize=11, leading=14,
            spaceAfter=16, textColor=colors.HexColor('#64748B'),
        ),
        'section': ParagraphStyle(
            'section', parent=base['Heading2'],
            fontName=_FONT_BOLD, fontSize=13, leading=16,
            spaceBefore=10, spaceAfter=6, textColor=colors.HexColor('#1E293B'),
        ),
        'body': ParagraphStyle(
            'body', parent=base['Normal'],
            fontName=_FONT_NAME, fontSize=10, leading=13,
            textColor=colors.HexColor('#0F172A'),
        ),
        'muted': ParagraphStyle(
            'muted', parent=base['Normal'],
            fontName=_FONT_NAME, fontSize=9, leading=12,
            textColor=colors.HexColor('#94A3B8'),
        ),
    }


def _localize(key: str, lang: str) -> str:
    """Простой словарик локалей для PDF. Не используем общую i18n чтобы
    не тащить I/O при каждом запросе."""
    labels = {
        'ru': {
            'title': 'Отчёт об успеваемости',
            'student': 'Ученик',
            'class': 'Класс',
            'period': 'Период',
            'generated': 'Сгенерирован',
            'source': 'Источник',
            'source_value': 'Google Sheets школы (через GradeSentinel bot)',
            'avg': 'Средний балл',
            'delta_up': 'выше прошлого периода на',
            'delta_down': 'ниже прошлого периода на',
            'delta_same': 'без изменений',
            'grades_count': 'Всего оценок за период',
            'no_data': 'Нет данных',
            'sec1': 'РАЗДЕЛ 1. СВОДКА',
            'sec2': 'РАЗДЕЛ 2. ЧЕТВЕРТНЫЕ ОЦЕНКИ',
            'sec3': 'РАЗДЕЛ 3. СВОДКА ПО ПРЕДМЕТАМ',
            'sec4': 'РАЗДЕЛ 4. ПОЛНАЯ ИСТОРИЯ ОЦЕНОК',
            'sec4_note': 'Все оценки за указанный период в хронологическом порядке. Этот раздел — основной для разрешения вопросов о точности данных.',
            'sec_problems': '⚠️ Проблемные предметы',
            'sec_top': '✨ Сильные предметы',
            'sec_subjects': 'По предметам',
            'sec_recent': 'Свежие оценки',
            'col_subject': 'Предмет',
            'col_avg': 'Средний',
            'col_count': 'Кол-во',
            'col_date': 'Дата',
            'col_grade': 'Оценка',
            'col_last': 'Последняя',
            'col_trend': 'Тренд',
            'col_q1': '1ч', 'col_q2': '2ч', 'col_q3': '3ч', 'col_q4': '4ч',
            'col_year': 'Год',
            'forecast_note': 'Значения с префиксом ~ — прогноз годовой оценки на основе текущих четвертных.',
            'trend_up': '↑',
            'trend_down': '↓',
            'trend_flat': '→',
            'footer': 'Документ автоматически сгенерирован GradeSentinel · {date}',
            'footer_proof': 'Этот документ содержит все оценки полученные из Google Sheets школы за указанный период. Использовать для предметного разговора с учителем или администрацией.',
            'status_concern': '⚠️ Есть на что обратить внимание',
            'status_improving': '📈 Тенденция улучшается',
            'status_declining': '📉 Снижение по сравнению с прошлым периодом',
            'status_stable': '✅ Всё стабильно',
        },
        'uz': {
            'title': "O'qish hisoboti",
            'student': "O'quvchi",
            'class': 'Sinf',
            'period': 'Davr',
            'generated': 'Yaratildi',
            'source': 'Manba',
            'source_value': "Maktab Google Sheets'i (GradeSentinel bot orqali)",
            'avg': "O'rtacha baho",
            'delta_up': "oldingi davrdan",
            'delta_down': "oldingi davrdan kam",
            'delta_same': "o'zgarishsiz",
            'grades_count': 'Davr davomida baholar',
            'no_data': "Ma'lumot yo'q",
            'sec1': "1-BO'LIM. UMUMIY KO'RSATKICHLAR",
            'sec2': "2-BO'LIM. CHORAK BAHOLARI",
            'sec3': "3-BO'LIM. FANLAR BO'YICHA XULOSA",
            'sec4': "4-BO'LIM. BAHOLARNING TO'LIQ TARIXI",
            'sec4_note': "Belgilangan davr uchun barcha baholar xronologik tartibda. Bu bo'lim ma'lumotlarning aniqligini tasdiqlash uchun asosiy.",
            'sec_problems': "⚠️ Muammoli fanlar",
            'sec_top': '✨ Kuchli fanlar',
            'sec_subjects': "Fanlar bo'yicha",
            'sec_recent': 'Yangi baholar',
            'col_subject': 'Fan',
            'col_avg': "O'rtacha",
            'col_count': 'Soni',
            'col_date': 'Sana',
            'col_grade': 'Baho',
            'col_last': "Oxirgi",
            'col_trend': 'Trend',
            'col_q1': '1ch', 'col_q2': '2ch', 'col_q3': '3ch', 'col_q4': '4ch',
            'col_year': 'Yil',
            'forecast_note': "~ belgisi bilan — joriy chorak baholaridan yillik baho prognozi.",
            'trend_up': '↑', 'trend_down': '↓', 'trend_flat': '→',
            'footer': "Hujjat GradeSentinel tomonidan avtomatik yaratildi · {date}",
            'footer_proof': "Bu hujjat maktab Google Sheets'idan olingan barcha baholarni o'z ichiga oladi. O'qituvchi yoki ma'muriyat bilan aniq suhbat uchun foydalaning.",
            'status_concern': "⚠️ E'tibor berishga arziydi",
            'status_improving': '📈 Tendentsiya yaxshilanmoqda',
            'status_declining': '📉 Oldingi davrga nisbatan pasayish',
            'status_stable': "✅ Hammasi barqaror",
        },
        'en': {
            'title': 'Academic performance report',
            'student': 'Student',
            'class': 'Class',
            'period': 'Period',
            'generated': 'Generated',
            'source': 'Source',
            'source_value': "School's Google Sheets (via GradeSentinel bot)",
            'avg': 'Average',
            'delta_up': 'higher than previous by',
            'delta_down': 'lower than previous by',
            'delta_same': 'unchanged',
            'grades_count': 'Total grades for period',
            'no_data': 'No data',
            'sec1': 'SECTION 1. SUMMARY',
            'sec2': 'SECTION 2. QUARTERLY GRADES',
            'sec3': 'SECTION 3. BY-SUBJECT BREAKDOWN',
            'sec4': 'SECTION 4. FULL GRADE HISTORY',
            'sec4_note': 'All grades for the specified period in chronological order. Primary section for resolving questions about data accuracy.',
            'sec_problems': '⚠️ Problem subjects',
            'sec_top': '✨ Strong subjects',
            'sec_subjects': 'By subject',
            'sec_recent': 'Recent grades',
            'col_subject': 'Subject',
            'col_avg': 'Average',
            'col_count': 'Count',
            'col_date': 'Date',
            'col_grade': 'Grade',
            'col_last': 'Last',
            'col_trend': 'Trend',
            'col_q1': 'Q1', 'col_q2': 'Q2', 'col_q3': 'Q3', 'col_q4': 'Q4',
            'col_year': 'Year',
            'forecast_note': 'Values with ~ prefix — forecast of year grade based on current quarterly grades.',
            'trend_up': '↑', 'trend_down': '↓', 'trend_flat': '→',
            'footer': 'Document automatically generated by GradeSentinel · {date}',
            'footer_proof': "This document contains all grades from the school's Google Sheets for the specified period. Use for substantive conversation with teacher or administration.",
            'status_concern': '⚠️ Worth attention',
            'status_improving': '📈 Trend improving',
            'status_declining': '📉 Decline vs previous period',
            'status_stable': '✅ All stable',
        },
    }
    return labels.get(lang, labels['ru']).get(key, key)


def _grade_color(avg: Optional[float]) -> colors.Color:
    """Цвет для среднего балла — соответствует webapp UI."""
    if avg is None:
        return colors.HexColor('#94A3B8')
    if avg >= 4.5:
        return colors.HexColor('#10B981')  # green
    if avg >= 3.5:
        return colors.HexColor('#0EA5E9')  # blue
    if avg >= 2.5:
        return colors.HexColor('#F59E0B')  # amber
    return colors.HexColor('#EF4444')  # red


def _hero_table(summary: Dict[str, Any], lang: str, styles: Dict[str, Any]) -> Table:
    """Большая «ячейка» сверху — средний балл + дельта."""
    current_avg = summary.get('current_avg')
    delta = summary.get('delta')

    avg_text = f"{current_avg:.1f}" if current_avg is not None else "—"
    if delta is None or abs(delta) < 0.05:
        delta_text = _localize('delta_same', lang)
    elif delta > 0:
        delta_text = f"{_localize('delta_up', lang)} {delta:+.1f}"
    else:
        delta_text = f"{_localize('delta_down', lang)} {abs(delta):.1f}"

    hex_color = _grade_color(current_avg).hexval()
    # .hexval() возвращает '0xRRGGBB' — берём только 6 hex-цифр + #
    color_attr = '#' + hex_color[-6:]
    avg_para = Paragraph(
        f'<font name="{_FONT_BOLD}" size="36" color="{color_attr}">{avg_text}</font>',
        styles['body'],
    )
    label_para = Paragraph(
        f'<font size="10">{_localize("avg", lang)}</font><br/>'
        f'<font size="9" color="#64748B">{delta_text}</font>',
        styles['body'],
    )

    tbl = Table([[avg_para, label_para]], colWidths=[40 * mm, 110 * mm])
    tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#E2E8F0')),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    return tbl


def _subjects_table(subjects: List[Dict[str, Any]], lang: str,
                    styles: Dict[str, Any]) -> Table:
    """Таблица «По предметам» — subject | avg | count."""
    header = [
        _localize('col_subject', lang),
        _localize('col_avg', lang),
        _localize('col_count', lang),
    ]
    rows = [header]
    for s in subjects:
        avg = s.get('avg')
        rows.append([
            s.get('name', '?'),
            f"{avg:.2f}" if isinstance(avg, (int, float)) else '—',
            str(s.get('count', 0)),
        ])
    tbl = Table(rows, colWidths=[100 * mm, 30 * mm, 30 * mm])
    tbl.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), _FONT_BOLD),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#0F172A')),
        ('FONTNAME', (0, 1), (-1, -1), _FONT_NAME),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#1E293B')),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#CBD5E1')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    return tbl


def _recent_table(grades: List[Dict[str, Any]], lang: str,
                  styles: Dict[str, Any], max_rows: int = 30) -> Table:
    header = [
        _localize('col_date', lang),
        _localize('col_subject', lang),
        _localize('col_grade', lang),
    ]
    rows = [header]
    for g in grades[:max_rows]:
        date_str = g.get('grade_date') or (g.get('date_added') or '')[:10]
        rows.append([
            date_str,
            g.get('subject', '?'),
            str(g.get('raw_text', '?')),
        ])
    tbl = Table(rows, colWidths=[35 * mm, 95 * mm, 30 * mm])
    tbl.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), _FONT_BOLD),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
        ('FONTNAME', (0, 1), (-1, -1), _FONT_NAME),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#CBD5E1')),
        ('ALIGN', (2, 0), (2, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return tbl


def _short_list(items: List[Dict[str, Any]], styles: Dict[str, Any],
                lang: str) -> List[Any]:
    """«Проблемные / сильные» предметы — компактный список с avg."""
    if not items:
        return [Paragraph(_localize('no_data', lang), styles['muted'])]
    out = []
    for s in items[:5]:
        avg = s.get('avg')
        avg_str = f"{avg:.2f}" if isinstance(avg, (int, float)) else '—'
        out.append(Paragraph(
            f"<font name=\"{_FONT_BOLD}\">{s.get('name', '?')}</font> · {avg_str}",
            styles['body'],
        ))
    return out


def _quarters_table(quarters: List[Dict[str, Any]], lang: str,
                    styles: Dict[str, Any]) -> Table:
    """Таблица четвертных оценок: предмет × 1ч-4ч + год (или прогноз)."""
    header = [
        _localize('col_subject', lang),
        _localize('col_q1', lang),
        _localize('col_q2', lang),
        _localize('col_q3', lang),
        _localize('col_q4', lang),
        _localize('col_year', lang),
        _localize('col_trend', lang),
    ]
    rows = [header]
    for q in quarters:
        trend_sym = _localize(f"trend_{q.get('trend', 'flat')}", lang)
        rows.append([
            q.get('subject', '?'),
            q.get('q1') or '—',
            q.get('q2') or '—',
            q.get('q3') or '—',
            q.get('q4') or '—',
            q.get('year') or '—',
            trend_sym,
        ])
    tbl = Table(rows, colWidths=[60 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, 22 * mm, 16 * mm])
    tbl.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), _FONT_BOLD),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
        ('FONTNAME', (0, 1), (-1, -1), _FONT_NAME),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#CBD5E1')),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return tbl


def _full_history_table(grades: List[Dict[str, Any]], lang: str,
                         styles: Dict[str, Any]) -> Table:
    """РАЗДЕЛ 4 PDF — полная история оценок за период. Хронологически,
    БЕЗ лимита (главный proof-документ). На многостраничных report'ах
    Platypus автоматически разбивает на страницы."""
    header = [
        _localize('col_date', lang),
        _localize('col_subject', lang),
        _localize('col_grade', lang),
    ]
    # ASC по дате — для чтения сверху-вниз как timeline
    sorted_grades = sorted(grades, key=lambda g: g.get('grade_date') or
                            (g.get('date_added') or '')[:10])
    rows = [header]
    for g in sorted_grades:
        date_str = g.get('grade_date') or (g.get('date_added') or '')[:10]
        rows.append([
            date_str,
            g.get('subject', '?'),
            str(g.get('raw_text', '?')),
        ])

    tbl = Table(rows, colWidths=[30 * mm, 100 * mm, 30 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), _FONT_BOLD),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
        ('FONTNAME', (0, 1), (-1, -1), _FONT_NAME),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#CBD5E1')),
        ('ALIGN', (2, 0), (2, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return tbl


def build_dashboard_pdf(
    student_name: str,
    summary: Dict[str, Any],
    by_subject: List[Dict[str, Any]],
    recent: List[Dict[str, Any]],
    period_label: str,
    lang: str = 'ru',
    student_class: str = '',
    quarters: Optional[List[Dict[str, Any]]] = None,
    period_start: str = '',
    period_end: str = '',
) -> bytes:
    """Главная точка входа. Возвращает PDF как bytes — caller обёрнёт
    в Flask Response с правильными headers.

    Dashboard refactor: PDF теперь полный proof-документ из 4 разделов.
    Используется для разрешения споров с учителем/школой. quarters +
    student_class + period_start/end — новые параметры (optional для
    обратной совместимости с тестами)."""
    _ensure_font()
    styles = _styles()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"GradeSentinel — {student_name}",
        author='GradeSentinel',
    )

    story = []

    # ─── HEADER ──────────────────────────────────────────
    story.append(Paragraph(_localize('title', lang), styles['title']))

    # Метаданные документа — таблица 2-колонки label/value, чёткая структура
    meta_rows = [
        [_localize('student', lang) + ':', student_name or '—'],
    ]
    if student_class:
        meta_rows.append([_localize('class', lang) + ':', student_class])
    meta_rows.append([_localize('period', lang) + ':',
                       f"{period_start} — {period_end}" if period_start else period_label])
    meta_rows.append([_localize('generated', lang) + ':',
                       datetime.now().strftime('%d.%m.%Y %H:%M (Asia/Tashkent)')])
    meta_rows.append([_localize('source', lang) + ':',
                       _localize('source_value', lang)])
    meta_table = Table(meta_rows, colWidths=[40 * mm, 134 * mm])
    meta_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), _FONT_BOLD),
        ('FONTNAME', (1, 0), (1, -1), _FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#64748B')),
        ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#0F172A')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#0F172A')))
    story.append(Spacer(1, 10))

    # ─── РАЗДЕЛ 1: СВОДКА ─────────────────────────────────
    story.append(Paragraph(_localize('sec1', lang), styles['section']))
    story.append(_hero_table(summary, lang, styles))
    story.append(Spacer(1, 6))

    status_key = f"status_{summary.get('status', 'stable')}"
    status_text = _localize(status_key, lang)
    grades_count = (
        summary.get('total_grades') or
        sum(s.get('count', 0) for s in by_subject) or
        len(recent)
    )
    story.append(Paragraph(
        f"{status_text} · {_localize('grades_count', lang)}: <b>{grades_count}</b>",
        styles['body'],
    ))
    story.append(Spacer(1, 14))

    # ─── РАЗДЕЛ 2: ЧЕТВЕРТНЫЕ ОЦЕНКИ ─────────────────────
    if quarters:
        story.append(Paragraph(_localize('sec2', lang), styles['section']))
        story.append(_quarters_table(quarters, lang, styles))
        # Note про прогноз если есть forecast
        has_forecast = any(q.get('year_is_forecast') for q in quarters)
        if has_forecast:
            story.append(Spacer(1, 4))
            story.append(Paragraph(
                f"<i>{_localize('forecast_note', lang)}</i>",
                styles['muted'],
            ))
        story.append(Spacer(1, 14))

    # ─── РАЗДЕЛ 3: ПО ПРЕДМЕТАМ ──────────────────────────
    if by_subject:
        story.append(Paragraph(_localize('sec3', lang), styles['section']))
        story.append(_subjects_table(by_subject, lang, styles))
        story.append(Spacer(1, 14))

    # ─── РАЗДЕЛ 4: ПОЛНАЯ ИСТОРИЯ ────────────────────────
    if recent:
        story.append(Paragraph(_localize('sec4', lang), styles['section']))
        story.append(Paragraph(
            f"<i>{_localize('sec4_note', lang)}</i>",
            styles['muted'],
        ))
        story.append(Spacer(1, 4))
        story.append(_full_history_table(recent, lang, styles))

    # ─── FOOTER ──────────────────────────────────────────
    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#CBD5E1')))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        _localize('footer_proof', lang),
        styles['muted'],
    ))
    story.append(Spacer(1, 2))
    story.append(Paragraph(
        _localize('footer', lang).format(date=datetime.now().strftime('%d.%m.%Y %H:%M')),
        styles['muted'],
    ))

    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes
