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
            'title': 'Дневник GradeSentinel',
            'avg': 'Средний балл',
            'delta_up': 'выше прошлого периода на',
            'delta_down': 'ниже прошлого периода на',
            'delta_same': 'без изменений',
            'grades_count': 'Всего оценок за период',
            'period': 'Период',
            'no_data': 'Нет данных',
            'sec_problems': '⚠️ Проблемные предметы',
            'sec_top': '✨ Сильные предметы',
            'sec_subjects': 'По предметам',
            'sec_recent': 'Свежие оценки',
            'col_subject': 'Предмет',
            'col_avg': 'Средний',
            'col_count': 'Кол-во',
            'col_date': 'Дата',
            'col_grade': 'Оценка',
            'footer': 'Сгенерировано GradeSentinel · {date}',
            'status_concern': '⚠️ Есть на что обратить внимание',
            'status_improving': '📈 Тенденция улучшается',
            'status_declining': '📉 Снижение по сравнению с прошлым периодом',
            'status_stable': '✅ Всё стабильно',
        },
        'uz': {
            'title': "GradeSentinel kundalik",
            'avg': "O'rtacha baho",
            'delta_up': "oldingi davrdan",
            'delta_down': "oldingi davrdan kam",
            'delta_same': "o'zgarishsiz",
            'grades_count': 'Davr davomida baholar',
            'period': 'Davr',
            'no_data': "Ma'lumot yo'q",
            'sec_problems': "⚠️ Muammoli fanlar",
            'sec_top': '✨ Kuchli fanlar',
            'sec_subjects': 'Fanlar bo\'yicha',
            'sec_recent': 'Yangi baholar',
            'col_subject': 'Fan',
            'col_avg': "O'rtacha",
            'col_count': 'Soni',
            'col_date': 'Sana',
            'col_grade': 'Baho',
            'footer': "GradeSentinel orqali yaratildi · {date}",
            'status_concern': "⚠️ E'tibor berishga arziydi",
            'status_improving': '📈 Tendentsiya yaxshilanmoqda',
            'status_declining': '📉 Oldingi davrga nisbatan pasayish',
            'status_stable': "✅ Hammasi barqaror",
        },
        'en': {
            'title': 'GradeSentinel diary',
            'avg': 'Average',
            'delta_up': 'higher than previous by',
            'delta_down': 'lower than previous by',
            'delta_same': 'unchanged',
            'grades_count': 'Total grades for period',
            'period': 'Period',
            'no_data': 'No data',
            'sec_problems': '⚠️ Problem subjects',
            'sec_top': '✨ Strong subjects',
            'sec_subjects': 'By subject',
            'sec_recent': 'Recent grades',
            'col_subject': 'Subject',
            'col_avg': 'Average',
            'col_count': 'Count',
            'col_date': 'Date',
            'col_grade': 'Grade',
            'footer': 'Generated by GradeSentinel · {date}',
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


def build_dashboard_pdf(
    student_name: str,
    summary: Dict[str, Any],
    by_subject: List[Dict[str, Any]],
    recent: List[Dict[str, Any]],
    period_label: str,
    lang: str = 'ru',
) -> bytes:
    """Главная точка входа. Возвращает PDF как bytes — caller обёрнёт
    в Flask Response с правильными headers."""
    _ensure_font()
    styles = _styles()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"GradeSentinel — {student_name}",
        author='GradeSentinel',
    )

    story = []

    # Header
    story.append(Paragraph(student_name, styles['title']))
    story.append(Paragraph(
        f"{_localize('title', lang)} · {_localize('period', lang)}: {period_label}",
        styles['subtitle'],
    ))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#E2E8F0')))
    story.append(Spacer(1, 8))

    # Hero
    story.append(_hero_table(summary, lang, styles))
    story.append(Spacer(1, 8))

    # Status line + grades count
    status_key = f"status_{summary.get('status', 'stable')}"
    status_text = _localize(status_key, lang)
    grades_count = (
        summary.get('total_grades') or
        sum(s.get('count', 0) for s in by_subject) or
        len(recent)
    )
    story.append(Paragraph(
        f"{status_text} · {_localize('grades_count', lang)}: {grades_count}",
        styles['body'],
    ))
    story.append(Spacer(1, 12))

    # Problems / Top — compact section
    problems = summary.get('problem_subjects') or []
    tops = summary.get('top_subjects') or []
    if problems:
        story.append(Paragraph(_localize('sec_problems', lang), styles['section']))
        story.extend(_short_list(problems, styles, lang))
        story.append(Spacer(1, 8))
    if tops:
        story.append(Paragraph(_localize('sec_top', lang), styles['section']))
        story.extend(_short_list(tops, styles, lang))
        story.append(Spacer(1, 8))

    # By subject table
    if by_subject:
        story.append(Paragraph(_localize('sec_subjects', lang), styles['section']))
        story.append(_subjects_table(by_subject, lang, styles))
        story.append(Spacer(1, 12))

    # Recent grades
    if recent:
        story.append(Paragraph(_localize('sec_recent', lang), styles['section']))
        story.append(_recent_table(recent, lang, styles))

    # Footer
    story.append(Spacer(1, 18))
    story.append(HRFlowable(width="100%", thickness=0.3, color=colors.HexColor('#E2E8F0')))
    story.append(Paragraph(
        _localize('footer', lang).format(date=datetime.now().strftime('%d.%m.%Y %H:%M')),
        styles['muted'],
    ))

    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes
