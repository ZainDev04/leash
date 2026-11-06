const $ = id => document.getElementById(id);
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};

// Theme
const themes = ["light", "dark"];
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  if ($("theme-name")) $("theme-name").textContent = t;
}
let theme = themes.includes(store.get("leash-theme-v2")) ? store.get("leash-theme-v2") : "light";
applyTheme(theme);
if ($("theme")) $("theme").onclick = () => { theme = themes[(themes.indexOf(theme) + 1) % 2]; store.set("leash-theme-v2", theme); applyTheme(theme); };

// Glossy card: tilt and light follow the pointer
const card = $("card");
if (card && !reduced && matchMedia("(pointer: fine)").matches) {
  $("stage").addEventListener("pointermove", e => {
    const r = card.getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
    card.style.setProperty("--ry", `${(x - .5) * 26}deg`); card.style.setProperty("--rx", `${(.5 - y) * 20}deg`);
    card.style.setProperty("--gx", `${x * 100}%`); card.style.setProperty("--gy", `${y * 100}%`);
  });
  $("stage").addEventListener("pointerleave", () => ["--rx", "--ry", "--gx", "--gy"].forEach(p => card.style.removeProperty(p)));
}
