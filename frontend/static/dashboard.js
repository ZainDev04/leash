const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const money = n => "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
let draft = null, frozen = false, seen = null;

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (r.status === 401) { location.href = "/login"; throw new Error("Logged out"); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

const statusColor = { paid: "var(--paid)", waiting: "var(--wait)", blocked: "var(--stop)", cancelled: "var(--stop)", refunded: "var(--wait)" };
const statusWord = { paid: "Paid", waiting: "Waiting for you", blocked: "Stopped", cancelled: "Rejected", refunded: "Refunded", checking: "Checking", processing: "Working" };
let reactTimer;
function cardReact(p) {
  card.style.setProperty("--g", statusColor[p.status] || "var(--text-primary)");
  card.classList.remove("shake"); void card.offsetWidth;
  card.classList.add("react");
  if (p.status === "blocked" || p.status === "cancelled") card.classList.add("shake");
  $("card-status").querySelector("span").textContent = statusWord[p.status] || p.status;
  $("caption").textContent = `#${p.id} ${p.item}, ${money(p.amount)}: ${(statusWord[p.status] || p.status).toLowerCase()}.`;
  clearTimeout(reactTimer);
  reactTimer = setTimeout(() => {
    card.classList.remove("react");
    card.style.setProperty("--g", frozen ? "var(--stop)" : "var(--paid)");
    $("card-status").querySelector("span").textContent = frozen ? "Frozen" : "Active";
  }, 3000);
}

function toast(status, text) {
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<i style="background:${statusColor[status] || "var(--paid)"}"></i><span>${esc(text)}</span>`;
  $("toasts").appendChild(el);
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 260); }, 4000);
}

function countTo(el, value, fmt = v => Math.round(v)) {
  const from = Number(el.dataset.v || 0); el.dataset.v = value;
  if (reduced || from === value) { el.textContent = fmt(value); return; }
  const t0 = performance.now();
  const step = now => { const t = Math.min(1, (now - t0) / 1000), e = 1 - Math.pow(1 - t, 3); el.textContent = fmt(from + (value - from) * e); if (t < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}

// Only touch the DOM when the content changed, so hover states and buttons stay put between polls
function setHTML(el, html) { if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; } }

function ruleRows(p) {
  const list = (a, none) => a && a.length ? a.map(esc).join(", ") : none;
  return [["Max per payment", money(p.per_payment_limit)], ["Ask me above", money(p.ask_above)], ["Monthly budget", money(p.monthly_budget)],
          ["Categories", list(p.allowed_categories, "none")], ["Stores", list(p.allowed_merchants, "any not blocked")], ["Blocked stores", list(p.blocked_merchants, "none")]]
    .map(([k, v]) => `<li><span>${k}</span><b>${v}</b></li>`).join("");
}

async function loadStatus() {
  const s = await api("/status");
  frozen = s.frozen;
  document.body.classList.toggle("frozen", frozen);
  $("freeze").setAttribute("aria-checked", frozen);
  if (!card.classList.contains("react")) {
    card.style.setProperty("--g", frozen ? "var(--stop)" : "var(--paid)");
    $("card-status").querySelector("span").textContent = frozen ? "Frozen" : "Active";
  }
  $("vault").textContent = s.vault_saved ? "Connected" : "Not connected";
  $("vault-text").textContent = s.vault_saved ? "Reconnect PayPal" : "Connect PayPal";
  const budget = s.policy.monthly_budget || 1, used = Math.min(1, s.spent_this_month / budget);
  countTo($("spent"), s.spent_this_month, money);
  countTo($("left"), Math.max(0, budget - s.spent_this_month), money);
  countTo($("card-limit"), s.policy.per_payment_limit, money);
  $("meter").style.width = used * 100 + "%";
  $("meter").style.background = used > .9 ? "var(--stop)" : used > .7 ? "var(--wait)" : "var(--accent)";
  setHTML($("rule-list"), ruleRows(s.policy));
}

function flow(p) {
  const ai = p.reasons.filter(r => r.startsWith("AI:")), held = !!p.order_id;
  const f = [];
  f.push(!held && p.status === "blocked" ? ["no", "Rules"] : ["ok", "Rules"]);
  f.push(held ? ["ok", "Held"] : ["", "Held"]);
  f.push(!held ? ["", "AI check"] : ai.length ? [p.status === "blocked" ? "no" : "warn", "AI check"] : ["ok", "AI check"]);
  f.push({ paid: ["ok", "Paid"], waiting: ["warn", "Your call"], blocked: ["no", held ? "Hold released" : "Not charged"], cancelled: ["no", "Rejected"], refunded: ["warn", "Refunded"] }[p.status] || ["", p.status]);
  return f.map(([c, t]) => `<div class="${c}"><span>${t}</span></div>`).join("");
}
function why(p) {
  return p.reasons.map(r => {
    if (r.startsWith("AI ok:")) return `<div><b class="ai">AI</b><span>${esc(r.slice(6).trim())}</span></div>`;
    if (r.startsWith("AI:")) return `<div><b class="ai">AI</b><span>${esc(r.slice(3).trim())}</span></div>`;
    if (r.startsWith("You ")) return `<div><b>YOU</b><span>${esc(r)}</span></div>`;
    return `<div><b>RULE</b><span>${esc(r)}</span></div>`;
  }).join("");
}

async function loadPayments() {
  const list = await api("/payments");
  const first = seen === null;
  seen = seen || new Set();
  const fresh = list.filter(p => !seen.has(p.id + p.status));
  list.forEach(p => seen.add(p.id + p.status));
  const n = s => list.filter(p => p.status === s).length;
  countTo($("n-paid"), n("paid")); countTo($("n-wait"), n("waiting")); countTo($("n-stop"), n("blocked") + n("cancelled"));
  $("total").textContent = list.length ? `${list.length} payments` : "";
  if (!list.length) return;
  const freshIds = new Set(fresh.map(p => p.id));
  setHTML($("list"), list.map(p => `
    <article class="pay ${esc(p.status)} ${!first && freshIds.has(p.id) ? "enter" : ""}">
      <div class="pay-head">
        <div><div class="pay-item">${esc(p.item)}</div><div class="pay-meta">${esc(p.merchant)} &middot; ${esc(p.agent)} &middot; #${p.id}${p.page_verified ? " &middot; page read by Leash" : ""}</div></div>
        <div><div class="pay-amt num">${money(p.amount)}</div><span class="badge ${esc(p.status)}">${statusWord[p.status] || esc(p.status)}</span></div>
      </div>
      <div class="flow">${flow(p)}</div>
      <div class="why">${why(p)}</div>
      <div class="task">Task: ${esc(p.task)}</div>
      ${p.status === "waiting" ? `<div class="acts">
        <button class="btn go sm" data-pay="${p.id}" data-action="approve">Approve and pay</button>
        <button class="btn sm" data-pay="${p.id}" data-action="reject">Reject</button></div>` : ""}
      ${p.status === "paid" ? `<div class="acts"><button class="btn sm" data-pay="${p.id}" data-action="refund">Wrong item? Refund it</button></div>` : ""}
    </article>`).join(""));
  if (first) return;
  fresh.sort((a, b) => a.id - b.id).forEach((p, i) => setTimeout(() => {
    cardReact(p);
    toast(p.status, `${statusWord[p.status] || p.status}: ${p.item}, ${money(p.amount)}`);
  }, i * 1200));
}

async function decide(id, action, btn) {
  btn.disabled = true; btn.innerHTML = `<span class="spin"></span>${{ approve: "Paying", reject: "Releasing", refund: "Refunding" }[action]}`;
  try { await api(`/payments/${id}/${action}`, { method: "POST" }); } catch (e) { toast("blocked", e.message); }
  refresh();
}
$("list").addEventListener("click", e => {
  const btn = e.target.closest("[data-pay]");
  if (!btn) return;
  // A refund cannot be undone, so it takes a second click
  if (btn.dataset.action === "refund" && !btn.dataset.armed) {
    btn.dataset.armed = "1";
    btn.textContent = "Click again to refund";
    setTimeout(() => { if (btn.isConnected && !btn.disabled) { delete btn.dataset.armed; btn.textContent = "Wrong item? Refund it"; } }, 4000);
    return;
  }
  decide(btn.dataset.pay, btn.dataset.action, btn);
});
$("logout").onclick = async () => {
  await fetch("/logout", { method: "POST" });
  location.href = "/";
};
$("freeze").onclick = async () => {
  await api(frozen ? "/unfreeze" : "/freeze", { method: "POST" });
  toast(frozen ? "paid" : "blocked", frozen ? "Agents can pay again." : "All agent payments frozen.");
  refresh();
};
$("parse").onclick = async () => {
  const text = $("rules-text").value.trim(); if (!text) { $("rules-text").focus(); return; }
  const btn = $("parse"); btn.disabled = true; btn.innerHTML = `<span class="spin"></span>Reading`;
  try {
    draft = await api("/policy/parse", { method: "POST", body: JSON.stringify({ text }) });
    $("draft-summary").textContent = draft.summary; $("draft-list").innerHTML = ruleRows(draft); $("draft").hidden = false;
  } catch (e) { toast("blocked", "Could not read the rules: " + e.message); }
  btn.disabled = false; btn.textContent = "Read my rules";
};
$("confirm").onclick = async () => {
  await api("/policy", { method: "PUT", body: JSON.stringify(draft) });
  $("draft").hidden = true; $("rules-text").value = ""; toast("paid", "Rules saved. Every new payment uses them."); refresh();
};
$("discard").onclick = () => { $("draft").hidden = true; draft = null; };

let tgLinked = false;
async function loadTelegram() {
  const t = await api("/telegram");
  $("tg").hidden = !t.available;
  tgLinked = t.linked;
  $("tg-state").textContent = t.linked ? "Connected" : "Not connected";
  $("tg-link").textContent = t.linked ? "Disconnect Telegram" : "Connect Telegram";
  if (t.linked) { $("tg-open").hidden = true; $("tg-hint").hidden = true; }
}
$("tg-link").onclick = async () => {
  try {
    if (tgLinked) {
      await api("/telegram/unlink", { method: "POST" });
    } else {
      $("tg-open").href = (await api("/telegram/link", { method: "POST" })).url;
      $("tg-open").hidden = false; $("tg-hint").hidden = false;
    }
    loadTelegram();
  } catch (e) { toast("blocked", e.message); }
};
loadTelegram().catch(() => {});
setInterval(() => { if (!document.hidden) loadTelegram().catch(() => {}); }, 5000);

function refresh() { loadStatus().catch(() => {}); loadPayments().catch(() => {}); }
if (new URLSearchParams(location.search).get("vault") === "failed") {
  toast("blocked", "PayPal could not be saved. Try connecting again.");
  history.replaceState(null, "", "/app");
}
refresh();
setInterval(refresh, 2500);
