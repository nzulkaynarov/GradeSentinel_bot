export default function Home() {
  return (
    <main className="min-h-screen flex items-center justify-center px-6 py-24">
      <div className="max-w-2xl">
        <span className="inline-flex items-center gap-2 text-[11px] uppercase tracking-[0.14em] text-ink-3 font-mono">
          <span className="inline-block w-5 h-px bg-current opacity-40" />
          GradeSentinel · Web · Phase 0
        </span>
        <h1 className="font-display text-5xl md:text-7xl font-medium tracking-tight mt-6 leading-[0.98]">
          Скаффолд готов.
        </h1>
        <p className="text-lg text-ink-3 mt-6 max-w-prose leading-relaxed">
          Это пустая страница Next.js 15 (App Router) + Tailwind v4. В Phase 3
          сюда переедет содержимое из{" "}
          <code className="font-mono text-sm bg-paper-2 px-1.5 py-0.5 rounded">
            frontend/dashboard.html
          </code>
          {" "}— родительский кабинет под маршрутом <code className="font-mono text-sm bg-paper-2 px-1.5 py-0.5 rounded">/(portal)</code>.
          В Phase 4 — админка под <code className="font-mono text-sm bg-paper-2 px-1.5 py-0.5 rounded">/(admin)</code>.
        </p>
        <div className="flex gap-3 mt-10">
          <a
            href="https://t.me/GradeSentinel_bot"
            className="inline-flex items-center gap-2 h-12 px-6 rounded-full bg-ink text-paper text-sm font-semibold hover:bg-black transition-colors"
          >
            Открыть бота
          </a>
          <a
            href="/"
            className="inline-flex items-center gap-2 h-12 px-6 rounded-full border border-line-2 text-sm font-semibold hover:bg-black/5 transition-colors"
          >
            Документация
          </a>
        </div>
      </div>
    </main>
  );
}
