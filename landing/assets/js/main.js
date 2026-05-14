// Mobile hamburger menu
const topbar = document.querySelector(".topbar");
const toggle = document.querySelector(".topbar-toggle");
const topbarNav = document.querySelector(".topbar-nav");
if (topbar && toggle && topbarNav) {
  const closeMenu = () => {
    topbar.classList.remove("menu-open");
    document.body.classList.remove("nav-open");
    toggle.setAttribute("aria-expanded", "false");
  };
  toggle.addEventListener("click", () => {
    const isOpen = topbar.classList.toggle("menu-open");
    document.body.classList.toggle("nav-open", isOpen);
    toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
  });
  // Закрываем меню при клике на anchor-link (чтобы скролл сработал)
  topbarNav.querySelectorAll("a").forEach((a) => {
    a.addEventListener("click", closeMenu);
  });
  // Закрываем при ресайзе на desktop
  window.addEventListener("resize", () => {
    if (window.innerWidth > 768 && topbar.classList.contains("menu-open")) {
      closeMenu();
    }
  });
  // Закрываем по Escape
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && topbar.classList.contains("menu-open")) {
      closeMenu();
    }
  });
}

// Reveal on scroll
const io = new IntersectionObserver(
  (entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) {
        e.target.classList.add("in");
        io.unobserve(e.target);
      }
    });
  },
  { rootMargin: "0px 0px -10% 0px", threshold: 0.05 }
);
document.querySelectorAll(".reveal").forEach((el) => io.observe(el));

// FAQ accordion (мутекс — открыт один)
document.querySelectorAll(".faq-item").forEach((item) => {
  const q = item.querySelector(".faq-q");
  if (!q) return;
  q.addEventListener("click", () => {
    const wasOpen = item.classList.contains("open");
    document.querySelectorAll(".faq-item").forEach((i) => i.classList.remove("open"));
    if (!wasOpen) item.classList.add("open");
  });
});

// Docs TOC scroll spy (на странице /docs/ — иначе массив пустой)
const tocLinks = document.querySelectorAll(".doc-toc a");
const docSections = document.querySelectorAll(".doc-section");
if (tocLinks.length && docSections.length) {
  const updateActive = () => {
    let current = "";
    docSections.forEach((sec) => {
      const top = sec.getBoundingClientRect().top;
      if (top < 120) current = sec.id;
    });
    tocLinks.forEach((a) =>
      a.classList.toggle("active", a.getAttribute("href") === "#" + current)
    );
  };
  window.addEventListener("scroll", updateActive, { passive: true });
  updateActive();
}
