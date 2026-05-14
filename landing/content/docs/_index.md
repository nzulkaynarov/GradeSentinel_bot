---
title: "Документация"
summary: "Как настроить бота, добавить ребёнка, разобраться с уведомлениями и подпиской."
date: 2026-05-14
---

<section id="start" class="doc-section">
  <h2>1. С чего начать</h2>
  <p>GradeSentinel — это Telegram-бот. У вас уже есть Telegram, поэтому ничего устанавливать не нужно. Вся работа происходит в чате с ботом.</p>

  <div class="step-block">
    <div class="step-n">1</div>
    <div>
      <h3>Откройте бот в Telegram</h3>
      <p>Перейдите по ссылке <code>t.me/GradeSentinel_bot</code> или найдите бота в поиске Telegram. Нажмите кнопку <span class="kbd">START</span> внизу экрана.</p>
    </div>
  </div>

  <div class="step-block">
    <div class="step-n">2</div>
    <div>
      <h3>Выберите язык</h3>
      <p>При первом запуске бот спросит язык интерфейса: <b>Русский</b>, <b>Oʻzbek</b> или <b>English</b>. Язык можно сменить позже в меню → «Язык».</p>
    </div>
  </div>

  <div class="step-block">
    <div class="step-n">3</div>
    <div>
      <h3>Авторизация по номеру телефона</h3>
      <p>Бот попросит поделиться контактом. Это нужно, чтобы привязать аккаунт к семье. Telegram автоматически предложит кнопку — просто нажмите её.</p>
    </div>
  </div>

  <div class="note info">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
    <p><b>Что такое «семья»?</b> Это группа родственников и детей, которые получают уведомления об одних и тех же дневниках. Один родитель может состоять в нескольких семьях (например, со супругом и со своими родителями).</p>
  </div>
</section>

<section id="roles" class="doc-section">
  <h2>2. Роли в семье</h2>
  <p>В системе три роли. Сначала вы становитесь главой первой семьи — потом можете добавить родственников любой ролью.</p>

  <table class="roles-table">
    <thead>
      <tr>
        <th>Действие</th>
        <th>Глава</th>
        <th>Родственник</th>
        <th>Админ</th>
      </tr>
    </thead>
    <tbody>
      <tr><td>Получать уведомления</td><td class="y">✓</td><td class="y">✓</td><td class="y">✓</td></tr>
      <tr><td>Смотреть оценки <code>/grades</code></td><td class="y">✓</td><td class="y">✓</td><td class="y">✓</td></tr>
      <tr><td>AI-отчёт</td><td class="y">✓</td><td class="y">✓</td><td class="y">✓</td></tr>
      <tr><td>Добавлять детей</td><td class="y">✓</td><td class="n">—</td><td class="y">✓</td></tr>
      <tr><td>Создавать инвайт-ссылки</td><td class="y">✓</td><td class="n">—</td><td class="y">✓</td></tr>
      <tr><td>Удалять ребёнка из семьи</td><td class="y">✓</td><td class="n">—</td><td class="y">✓</td></tr>
      <tr><td>Управлять подпиской</td><td class="y">✓</td><td class="n">—</td><td class="y">✓</td></tr>
      <tr><td>Рассылка всем пользователям</td><td class="n">—</td><td class="n">—</td><td class="y">✓</td></tr>
    </tbody>
  </table>
</section>

<section id="first-family" class="doc-section">
  <h2>3. Создание первой семьи</h2>
  <p>В главном меню нажмите кнопку <span class="kbd">👨‍👩‍👧 Создать семью</span>. Бот попросит ввести название — обычно это фамилия. Например: «Каримовы».</p>

  <div class="tg-shot">
    <div class="tg-msg">
      <b>Создание семьи</b><br>
      Введите название семьи (например, «Каримовы»):
      <span class="tg-time">10:24</span>
    </div>
    <div class="tg-msg me">
      Каримовы
      <span class="tg-time">10:24 ✓✓</span>
    </div>
    <div class="tg-msg">
      ✅ Семья <b>«Каримовы»</b> создана.<br>
      Теперь добавьте первого ребёнка.
      <div class="tg-btn-row">
        <span class="tg-btn">➕ Добавить ребёнка</span>
        <span class="tg-btn">🔗 Пригласить родственника</span>
      </div>
      <span class="tg-time">10:24</span>
    </div>
  </div>

  <p>После создания вы становитесь <b>главой</b> этой семьи и получаете полный контроль: можно добавлять детей, приглашать родственников и управлять подпиской.</p>
</section>

<section id="add-child" class="doc-section">
  <h2>4. Добавление ребёнка и его дневника</h2>
  <p>Это самый важный шаг. Бот должен получить <b>ссылку на Google Таблицу</b>, в которой ведётся дневник ребёнка.</p>

  <div class="step-block">
    <div class="step-n">1</div>
    <div>
      <h3>Получите ссылку у школы</h3>
      <p>Школа выдаёт каждому ученику свой Google-лист с оценками. Обычно его шлёт классный руководитель в родительский чат. Откройте таблицу — она должна открыться у вас в браузере с возможностью просмотра.</p>
    </div>
  </div>

  <div class="step-block">
    <div class="step-n">2</div>
    <div>
      <h3>Скопируйте URL</h3>
      <p>Нажмите на адресную строку браузера и скопируйте весь адрес — он начинается с <code>https://docs.google.com/spreadsheets/...</code></p>
    </div>
  </div>

  <div class="step-block">
    <div class="step-n">3</div>
    <div>
      <h3>Отправьте ссылку боту</h3>
      <p>В меню нажмите <span class="kbd">➕ Добавить ребёнка</span>. Введите ФИО ребёнка, затем вставьте ссылку. Бот проверит доступ и сразу покажет, какие листы он нашёл («Сегодня», «Все оценки», «Четверти»).</p>
    </div>
  </div>

  <div class="note">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>
    <p><b>Доступ к таблице.</b> Если бот пишет «нет доступа» — попросите классного руководителя открыть просмотр для адреса <code>gradesentinel@*.iam.gserviceaccount.com</code> (выдаётся при регистрации) или сделать таблицу доступной по ссылке всем «как читателю».</p>
  </div>
</section>

<section id="invite" class="doc-section">
  <h2>5. Пригласить второго родителя</h2>
  <p>Глава семьи может пригласить любое количество родственников: второго родителя, бабушку, старшего брата.</p>

  <div class="step-block">
    <div class="step-n">1</div>
    <div>
      <h3>Сгенерируйте инвайт-ссылку</h3>
      <p>Меню → <span class="kbd">🔗 Пригласить</span>. Бот пришлёт одноразовую ссылку вида <code>t.me/GradeSentinel_bot?start=inv_AB12CD</code> со сроком жизни 48 часов.</p>
    </div>
  </div>

  <div class="step-block">
    <div class="step-n">2</div>
    <div>
      <h3>Отправьте ссылку</h3>
      <p>Любым способом — в WhatsApp, Telegram, SMS. Получатель кликает по ссылке, попадает в бота, проходит авторизацию по телефону и автоматически добавляется в вашу семью.</p>
    </div>
  </div>

  <div class="note success">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M5 13l4 4L19 7"/></svg>
    <p>После активации ссылка становится недействительной. Если родственников несколько — генерируйте отдельную ссылку каждому.</p>
  </div>
</section>

<section id="notifications" class="doc-section">
  <h2>6. Какие уведомления приходят</h2>

  <h3>Новая оценка</h3>
  <p>Приходит в течение 5 минут после того, как учитель добавит её в дневник.</p>
  <div class="tg-shot">
    <div class="tg-msg">
      <b>📚 Новая оценка</b><br>
      У <b>Алишера</b> по математике: <span style="display: inline-block; background: #dcfce7; color: #15803d; padding: 0 8px; border-radius: 6px; font-weight: 700;">5</span><br>
      Контрольная работа · 2 четверть
      <span class="tg-time">10:24</span>
    </div>
  </div>

  <h3>Изменённая оценка</h3>
  <p>Если учитель исправил оценку в дневнике — бот сообщит об изменении.</p>
  <div class="tg-shot">
    <div class="tg-msg">
      <b>✏️ Оценка изменена</b><br>
      У <b>Камилы</b> по истории: было <span style="text-decoration: line-through; opacity: .6;">3</span> → стало <span style="display: inline-block; background: #dbeafe; color: #1d4ed8; padding: 0 8px; border-radius: 6px; font-weight: 700;">4</span>
      <span class="tg-time">14:02</span>
    </div>
  </div>

  <h3>Вечерняя сводка (19:00)</h3>
  <p>Каждый день в 19:00 по Ташкенту — общий дайджест всех оценок за день по каждому ребёнку. Не приходит, если оценок не было.</p>

  <h3>Тихие часы 22:00–07:00</h3>
  <div class="note info">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
    <p>С 22:00 до 07:00 (Ташкент, UTC+5) бот не присылает уведомления — они копятся в очереди и приходят утром в 7:00 одним сообщением. Это поведение нельзя отключить.</p>
  </div>
</section>

<section id="ai" class="doc-section">
  <h2>7. AI-аналитика</h2>
  <p>На платных тарифах бот умеет делать аналитический отчёт через Claude AI: какие предметы «проседают», есть ли тренд на улучшение, на что обратить внимание.</p>

  <h3>Отчёт по запросу</h3>
  <p>Команда <code>/ai_report</code> или кнопка <span class="kbd">🧠 AI-анализ</span> в меню. Бот возьмёт оценки за последние 14 дней и вернёт отчёт за 10–20 секунд.</p>

  <h3>Еженедельный AI-отчёт</h3>
  <p>Каждое воскресенье в 19:00 — автоматический отчёт за неделю. Приходит главе семьи и всем родственникам с активной подпиской.</p>

  <div class="note">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 9v2m0 4h.01"/><circle cx="12" cy="12" r="10"/></svg>
    <p>AI работает только при активной подписке. Без подписки кнопка скрывается, мониторинг новых оценок тоже отключается, но история сохраняется.</p>
  </div>
</section>

<section id="payment" class="doc-section">
  <h2>8. Подписка и оплата</h2>
  <p>Меню → <span class="kbd">💳 Подписка</span>. Доступны три тарифа:</p>
  <ul>
    <li><b>1 месяц</b> — 29 900 UZS</li>
    <li><b>3 месяца</b> — 79 900 UZS (экономия 11%)</li>
    <li><b>12 месяцев</b> — 249 900 UZS (экономия 30%)</li>
  </ul>
  <p>Оплата проходит через <b>Click</b> или <b>Payme</b> прямо в окне Telegram. После оплаты подписка активируется автоматически — без перезапуска бота.</p>

  <div class="note success">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M5 13l4 4L19 7"/></svg>
    <p><b>Без автопродления.</b> Подписка просто истекает в указанную дату — деньги не списываются повторно. За 3 дня до окончания бот напомнит и предложит продлить.</p>
  </div>
</section>

<section id="commands" class="doc-section">
  <h2>9. Команды бота</h2>
  <div class="cmd-list">
    <div class="cmd-item"><code>/start</code><span class="desc">Главное меню</span></div>
    <div class="cmd-item"><code>/help</code><span class="desc">Справка по роли</span></div>
    <div class="cmd-item"><code>/grades</code><span class="desc">Оценки за сегодня</span></div>
    <div class="cmd-item"><code>/ai_report</code><span class="desc">AI-анализ за 14 дней</span></div>
    <div class="cmd-item"><code>/manage_family</code><span class="desc">Управление семьёй</span></div>
    <div class="cmd-item"><code>/subscription</code><span class="desc">Подписка и тарифы</span></div>
    <div class="cmd-item"><code>/status</code><span class="desc">Ваша статистика</span></div>
    <div class="cmd-item"><code>/lang</code><span class="desc">Сменить язык</span></div>
  </div>
</section>

<section id="troubleshoot" class="doc-section">
  <h2>10. Если что-то не работает</h2>

  <h3>Бот не присылает оценки</h3>
  <ul>
    <li>Проверьте, активна ли подписка: <code>/subscription</code>. Без подписки мониторинг отключён.</li>
    <li>Убедитесь, что у бота есть доступ к Google Таблице.</li>
    <li>Проверьте, что сейчас не тихие часы (22:00–07:00) — оценки копятся и придут утром.</li>
    <li>Учитель действительно выставил оценку? Откройте таблицу вручную и убедитесь.</li>
  </ul>

  <h3>Не открывается дашборд (Mini App)</h3>
  <ul>
    <li>Обновите Telegram до последней версии.</li>
    <li>Откройте дашборд через кнопку в меню бота, а не по прямой ссылке — без подписи Telegram авторизация не сработает.</li>
  </ul>

  <h3>Связаться с поддержкой</h3>
  <p>Меню → <span class="kbd">💬 Поддержка</span>. Сообщение уходит в закрытую группу администраторов, ответ приходит в чат с ботом обычно в течение 1–2 часов в рабочее время.</p>

  <div class="note danger">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
    <p>Никогда не отправляйте боту пароли от Google-аккаунта или школьной системы. Боту нужна только <b>ссылка</b> на таблицу с открытым доступом.</p>
  </div>
</section>
