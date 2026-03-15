# Plan: Claude AI Analytics + WebApp + Multilingual Support

**Date:** 2026-03-15
**Status:** Draft for review

---

## Feature 1: Claude AI Analytics (weekly/daily grade analysis)

### Concept
Claude API analyzes grade trends per student and generates human-readable insights for parents:
- "Mathematics has been declining for 2 weeks (avg 4.5 -> 3.8)"
- "5 consecutive A's in Literature — great streak!"
- Weekly summary report sent Sunday evening

### Architecture

```
grade_history (DB) --> analytics_engine.py --> Claude API --> Telegram notification
                        ^                        |
                        |                        v
              Scheduler (Sunday 19:00)    Structured insight text
```

### New files
```
src/analytics_engine.py    # Data aggregation + Claude API calls
src/handlers/analytics.py  # /report command + scheduled reports
```

### Implementation steps

1. **Add `anthropic` to requirements.txt**, add `ANTHROPIC_API_KEY` to .env

2. **Create `src/analytics_engine.py`**:
   ```python
   import anthropic
   from src.database_manager import get_grade_history_for_student

   client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

   def analyze_student_grades(student_id: int, student_name: str) -> str:
       """Fetches last 14 days of grades, sends to Claude for analysis."""
       grades = get_grade_history_for_student(student_id, days=14)
       if not grades:
           return None

       # Format grades as structured data for Claude
       grade_text = "\n".join(
           f"{g['date_added']}: {g['subject']} = {g['raw_text']} ({g['grade_value']})"
           for g in grades
       )

       message = client.messages.create(
           model="claude-haiku-4-5-20251001",  # fast + cheap for analytics
           max_tokens=500,
           messages=[{
               "role": "user",
               "content": f"""Analyze these school grades for student {student_name}.
               Provide a brief (3-5 sentences) analysis in the same language as the data.
               Focus on: trends, best/worst subjects, streaks.
               Be encouraging but honest.

               Grades (last 14 days):
               {grade_text}"""
           }]
       )
       return message.content[0].text
   ```

3. **Add DB function** `get_grade_history_for_student(student_id, days)` to database_manager.py:
   ```python
   def get_grade_history_for_student(student_id: int, days: int = 14) -> List[Dict]:
       with get_db_connection() as conn:
           cursor = conn.cursor()
           cursor.execute('''
               SELECT subject, grade_value, raw_text, date_added
               FROM grade_history
               WHERE student_id = ? AND date_added >= datetime('now', ?)
               ORDER BY date_added
           ''', (student_id, f'-{days} days'))
           return [dict(row) for row in cursor.fetchall()]
   ```

4. **Create handler** `src/handlers/analytics.py`:
   - Button "📊 AI-анализ" — on-demand report for a specific child
   - Scheduled weekly report (Sunday 19:00) via threading.Timer or schedule library

5. **Cost estimate**: Claude Haiku at ~$0.25/M input tokens.
   - ~200 tokens per student per analysis
   - 50 students weekly = ~10K tokens = ~$0.003/week

### Environment changes
```env
ANTHROPIC_API_KEY=sk-ant-...
```

### Docker changes
Add to docker-compose.yml environment section:
```yaml
- ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
```

---

## Feature 2: Telegram Mini App (WebApp) — Grade Dashboard

### Concept
Interactive web dashboard opened via Telegram WebApp button:
- Grade charts per subject (line chart, last 30 days)
- Average grade per subject (bar chart)
- Today's grades summary
- Mobile-first, fits Telegram's WebApp panel

### Architecture

```
Telegram Bot
    |
    ├── WebApp Button (InlineKeyboardButton with web_app url)
    |
    v
Flask/FastAPI server (lightweight)
    ├── GET /webapp?user_id=X&token=Y  --> HTML+JS page
    ├── GET /api/grades?student_id=X   --> JSON data
    └── Static: Chart.js + CSS
```

### New files
```
webapp/
├── app.py              # Flask/FastAPI server
├── templates/
│   └── dashboard.html  # Main dashboard (Chart.js)
├── static/
│   ├── style.css
│   └── app.js          # Chart rendering logic
```

### Implementation steps

1. **Add dependencies**: `flask` (lightweight) to requirements.txt

2. **Create `webapp/app.py`**:
   ```python
   from flask import Flask, render_template, jsonify, request
   import hashlib, hmac
   from src.database_manager import get_students_for_parent, get_grade_history_for_student

   app = Flask(__name__)

   def validate_webapp_data(init_data: str, bot_token: str) -> bool:
       """Validate Telegram WebApp initData signature."""
       # Standard Telegram WebApp validation
       ...

   @app.route('/webapp')
   def dashboard():
       return render_template('dashboard.html')

   @app.route('/api/grades/<int:student_id>')
   def api_grades(student_id):
       days = request.args.get('days', 30, type=int)
       grades = get_grade_history_for_student(student_id, days=days)
       return jsonify(grades)
   ```

3. **Create `webapp/templates/dashboard.html`**:
   - Uses Telegram WebApp JS SDK (`telegram-web-app.js`)
   - Reads `Telegram.WebApp.initDataUnsafe.user.id` for auth
   - Fetches `/api/grades` and renders Chart.js line/bar charts
   - Responsive CSS for Telegram panel (320-420px width)

4. **Add WebApp button** to bot UI (in `ui.py`):
   ```python
   webapp_btn = types.InlineKeyboardButton(
       "📊 Дашборд оценок",
       web_app=types.WebAppInfo(url="https://your-domain.com/webapp")
   )
   ```

5. **Docker**: Add webapp service to docker-compose.yml:
   ```yaml
   webapp:
     build: ./webapp
     ports:
       - "8443:8443"
     volumes:
       - sentinel_data:/app/data
   ```

6. **SSL requirement**: Telegram WebApps require HTTPS.
   Options: Cloudflare Tunnel (free), Let's Encrypt, or ngrok for dev.

### Key considerations
- WebApp requires a public HTTPS endpoint
- For Raspberry Pi deployment: use Cloudflare Tunnel (free, no port forwarding)
- Validate `initData` from Telegram to prevent unauthorized access
- Chart.js CDN (~60KB) loads fast even on slow connections

---

## Feature 3: Multilingual Support (RU / UZ / EN)

### Concept
Each user selects their language at first launch or via settings.
All bot messages rendered in chosen language. Default: Russian.

### Architecture

```
User sends message
    |
    v
get_user_lang(user_id) --> "ru" / "uz" / "en"
    |
    v
t("key", lang) --> Localized string
    |
    v
Bot responds in user's language
```

### New files
```
src/i18n.py              # Translation engine
src/locales/
├── ru.json              # Russian (default, current texts)
├── uz.json              # Uzbek
└── en.json              # English
```

### Implementation steps

1. **Create `src/i18n.py`** — translation function:
   ```python
   import json
   import os
   from typing import Optional

   _translations = {}
   SUPPORTED_LANGS = ['ru', 'uz', 'en']
   DEFAULT_LANG = 'ru'

   def load_translations():
       locales_dir = os.path.join(os.path.dirname(__file__), 'locales')
       for lang in SUPPORTED_LANGS:
           path = os.path.join(locales_dir, f'{lang}.json')
           with open(path, 'r', encoding='utf-8') as f:
               _translations[lang] = json.load(f)

   def t(key: str, lang: str = DEFAULT_LANG, **kwargs) -> str:
       """Get translated string by key with optional formatting."""
       text = _translations.get(lang, {}).get(key)
       if text is None:
           text = _translations.get(DEFAULT_LANG, {}).get(key, key)
       if kwargs:
           text = text.format(**kwargs)
       return text
   ```

2. **Create locale files** — example `src/locales/ru.json`:
   ```json
   {
     "welcome": "Привет! Я GradeSentinel. Для работы мне нужно подтвердить, что вы есть в нашей базе.",
     "auth_success": "✅ Авторизация успешна! Здравствуйте, {name}.",
     "auth_admin": "👑 Вы авторизованы как <b>Супер-администратор</b>.",
     "auth_head": "🏠 Вы авторизованы как <b>Глава семьи</b>.",
     "phone_not_found": "❌ Извините, ваш номер не найден в базе данных.",
     "contact_admin": "📩 Написать администратору",
     "btn_status": "📊 Статус",
     "btn_families": "🏠 Семьи",
     "btn_new_family": "➕ Новая семья",
     "btn_my_family": "🏠 Моя семья",
     "btn_grades": "📈 Оценки",
     "btn_support": "💬 Поддержка",
     "btn_broadcast": "📢 Рассылка",
     "btn_settings": "⚙️ Настройки",
     "new_grade": "🔔 <b>Новая запись в дневнике!</b>\n👨‍🎓 Ученик: {name}\n📚 Предмет: {subject}\n📝 Значение: {value}",
     "rate_limited": "⏳ Слишком много запросов. Пожалуйста, подождите.",
     "lang_select": "🌐 Выберите язык / Tilni tanlang / Select language:",
     "lang_changed": "✅ Язык изменён на Русский."
   }
   ```

   Example `src/locales/uz.json`:
   ```json
   {
     "welcome": "Salom! Men GradeSentinel. Ishlash uchun sizni bazamizda borligini tasdiqlashim kerak.",
     "auth_success": "✅ Avtorizatsiya muvaffaqiyatli! Assalomu alaykum, {name}.",
     "auth_admin": "👑 Siz <b>Super administrator</b> sifatida avtorizatsiya qildingiz.",
     "auth_head": "🏠 Siz <b>Oila boshlig'i</b> sifatida avtorizatsiya qildingiz.",
     "phone_not_found": "❌ Kechirasiz, telefon raqamingiz bazada topilmadi.",
     "contact_admin": "📩 Administratorga yozish",
     "btn_status": "📊 Holat",
     "btn_families": "🏠 Oilalar",
     "btn_new_family": "➕ Yangi oila",
     "btn_my_family": "🏠 Mening oilam",
     "btn_grades": "📈 Baholar",
     "btn_support": "💬 Qo'llab-quvvatlash",
     "btn_broadcast": "📢 Tarqatma",
     "btn_settings": "⚙️ Sozlamalar",
     "new_grade": "🔔 <b>Kundalikda yangi yozuv!</b>\n👨‍🎓 O'quvchi: {name}\n📚 Fan: {subject}\n📝 Qiymati: {value}",
     "rate_limited": "⏳ Juda ko'p so'rovlar. Iltimos, bir necha soniya kuting.",
     "lang_select": "🌐 Выберите язык / Tilni tanlang / Select language:",
     "lang_changed": "✅ Til o'zbekchaga o'zgartirildi."
   }
   ```

3. **Add `lang` column to `parents` table**:
   ```python
   # Migration in init_db():
   cursor.execute("PRAGMA table_info(parents)")
   columns = [col[1] for col in cursor.fetchall()]
   if 'lang' not in columns:
       cursor.execute("ALTER TABLE parents ADD COLUMN lang TEXT DEFAULT 'ru'")
   ```

   ```python
   def get_user_lang(telegram_id: int) -> str:
       with get_db_connection() as conn:
           cursor = conn.cursor()
           cursor.execute('SELECT lang FROM parents WHERE telegram_id = ?', (telegram_id,))
           row = cursor.fetchone()
           return row['lang'] if row and row['lang'] else 'ru'

   def set_user_lang(telegram_id: int, lang: str):
       with get_db_connection() as conn:
           cursor = conn.cursor()
           cursor.execute('UPDATE parents SET lang = ? WHERE telegram_id = ?', (lang, telegram_id))
   ```

4. **Add language selection UI**:
   - During first `/start` — before phone verification
   - Via "⚙️ Настройки" button in main menu (new button)
   - Inline keyboard with 3 flags:

   ```python
   def lang_select_markup():
       markup = types.InlineKeyboardMarkup()
       markup.row(
           types.InlineKeyboardButton("🇷🇺 Русский", callback_data="set_lang_ru"),
           types.InlineKeyboardButton("🇺🇿 O'zbek", callback_data="set_lang_uz"),
           types.InlineKeyboardButton("🇬🇧 English", callback_data="set_lang_en"),
       )
       return markup
   ```

5. **Refactor all hardcoded strings** — replace with `t()` calls:
   ```python
   # Before:
   bot.send_message(user_id, "⏳ Слишком много запросов.")

   # After:
   lang = get_user_lang(user_id)
   bot.send_message(user_id, t("rate_limited", lang))
   ```

6. **Localize button texts** — since reply keyboard buttons are matched by text,
   the `handle_menu_buttons` handler needs to match all languages:
   ```python
   # Map localized button text -> action
   BUTTON_ACTIONS = {}
   for lang_code in SUPPORTED_LANGS:
       BUTTON_ACTIONS[t("btn_grades", lang_code)] = "grades"
       BUTTON_ACTIONS[t("btn_status", lang_code)] = "status"
       ...

   @bot.message_handler(func=lambda m: m.text in BUTTON_ACTIONS)
   def handle_menu_buttons(message):
       action = BUTTON_ACTIONS[message.text]
       ...
   ```

### Migration strategy (non-breaking)
1. Add `lang` column with default `'ru'` — all existing users stay Russian
2. Add "⚙️ Настройки" to menu — users can switch language themselves
3. Language selection also shown at `/start` for new users
4. All locale JSON files start as copy of Russian, then translate

---

## Integration order (recommended)

```
Phase 1: i18n foundation (Feature 3)
  └── This touches all files, so do it first
  └── Start with ru.json (copy existing strings)
  └── Then translate to uz.json and en.json

Phase 2: Claude AI Analytics (Feature 1)
  └── Standalone module, minimal changes to existing code
  └── Uses i18n for output messages
  └── Claude prompt language matches user's lang setting

Phase 3: WebApp Dashboard (Feature 2)
  └── Most complex (separate web server, SSL, frontend)
  └── Uses same DB and grade_history data
  └── i18n for webapp via browser Accept-Language or Telegram lang
```

---

## Estimated effort

| Feature | Files to create | Files to modify | Complexity |
|---------|----------------|-----------------|------------|
| Claude AI Analytics | 2 | 3 | Medium |
| WebApp Dashboard | 4+ | 2 | High (SSL, frontend) |
| Multilingual (i18n) | 4 | 8 (all handlers) | Medium-High (many string replacements) |

## New dependencies

```txt
# requirements.txt additions:
anthropic          # Claude AI API
flask              # WebApp server (or fastapi + uvicorn)
```
