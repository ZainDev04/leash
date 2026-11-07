// Audit log table. Cell values go to AG Grid as plain text and no column uses an HTML renderer,
// so an item name written by an agent or a shop cannot run as code on this page.
const AUDIT_POLL_MS = 5000;
const stopped = ["blocked", "cancelled"];
const inGroup = { all: () => true, paid: s => s === "paid", waiting: s => s === "waiting", stopped: s => stopped.includes(s), refunded: s => s === "refunded" };
let auditGroup = "all", idsShown = false;

const toDate = text => text ? new Date(text.replace(" ", "T") + "Z") : null;
const moneyOrBlank = p => p.value == null ? "" : money(p.value);
// Numbers and dates open newest or biggest first. A click only ever flips the order, so the arrow never vanishes.
const biggestFirst = ["desc", "asc"];
// The date filter hands over midnight, so compare the day and ignore the time of the payment
const sameDayCompare = (day, cell) => {
  if (!cell) return -1;
  const cellDay = new Date(cell.getFullYear(), cell.getMonth(), cell.getDate());
  return cellDay < day ? -1 : cellDay > day ? 1 : 0;
};

function decidedBy(p) {
  if (!p.reasons) return "";
  if (p.reasons.some(r => r.startsWith("You "))) return "You";
  if (p.reasons.some(r => r.startsWith("AI:"))) return "AI";
  return p.status === "paid" ? "Rules and AI" : "Rule";
}
function priceChange(p) {
  if (p.quoted_price == null || p.amount == null) return null;
  const change = Math.round((p.amount - p.quoted_price) * 100) / 100;
  return change === 0 ? null : change;
}
const readableReasons = p => (p.reasons || []).map(r => r.replace(/^AI( ok)?:\s*/, "")).reverse().join(" ");

const auditColumns = [
  { field: "id", headerName: "#", width: 132, minWidth: 120, filter: "agNumberColumnFilter", sort: "desc", sortingOrder: biggestFirst },
  { colId: "when", headerName: "When (your time)", width: 210, filter: "agDateColumnFilter", sortingOrder: biggestFirst,
    filterParams: { comparator: sameDayCompare },
    valueGetter: p => toDate(p.data.created_at),
    valueFormatter: p => p.value ? p.value.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "" },
  { field: "agent", headerName: "Agent", width: 205 },
  { field: "item", headerName: "Item", width: 285, tooltipField: "item" },
  { field: "amount", headerName: "Amount", width: 140, type: "rightAligned", filter: "agNumberColumnFilter", sortingOrder: biggestFirst, valueFormatter: moneyOrBlank },
  { colId: "result", headerName: "Result", width: 155,
    valueGetter: p => p.data.status ? (statusWord[p.data.status] || p.data.status) : "",
    cellClassRules: {
      "cell-paid": p => p.data.status === "paid",
      "cell-waiting": p => p.data.status === "waiting" || p.data.status === "refunded",
      "cell-stopped": p => stopped.includes(p.data.status),
    } },
  { colId: "decided", headerName: "Decided by", width: 140, valueGetter: p => decidedBy(p.data) },
  { colId: "reasons", headerName: "Reasons", flex: 1, minWidth: 300, valueGetter: p => readableReasons(p.data), tooltipValueGetter: p => p.value },
  { field: "merchant", headerName: "Store", width: 160 },
  { field: "category", headerName: "Category", width: 140 },
  { field: "quoted_price", headerName: "Quoted", width: 140, type: "rightAligned", filter: "agNumberColumnFilter", sortingOrder: biggestFirst, valueFormatter: moneyOrBlank },
  { colId: "change", headerName: "Price change", width: 135, type: "rightAligned", filter: "agNumberColumnFilter", sortingOrder: biggestFirst,
    valueGetter: p => priceChange(p.data),
    valueFormatter: p => p.value == null ? "" : (p.value > 0 ? "+" : "-") + money(Math.abs(p.value)),
    cellClassRules: { "cell-stopped": p => p.value > 0 } },
  { colId: "page", headerName: "Page read by Leash", width: 165,
    valueGetter: p => p.data.status ? (p.data.page_verified ? "Yes" : "No") : "" },
  { colId: "confirmed", headerName: "PayPal reported", width: 220,
    valueGetter: p => (p.data.paypal_events || []).map(e => e.label).join(", "), tooltipValueGetter: p => p.value },
  { field: "order_id", headerName: "PayPal order", width: 190, hide: true },
  { field: "capture_id", headerName: "PayPal capture", width: 190, hide: true },
  { field: "refund_id", headerName: "PayPal refund", width: 190, hide: true },
];

const auditTheme = agGrid.themeQuartz.withParams({
  backgroundColor: "var(--surface-base)",
  foregroundColor: "var(--text-primary)",
  headerBackgroundColor: "var(--surface-raised)",
  headerTextColor: "var(--text-secondary)",
  borderColor: "var(--line-strong)",
  rowHoverColor: "var(--surface-raised)",
  selectedRowBackgroundColor: "var(--surface-muted)",
  accentColor: "var(--accent)",
  fontFamily: "Poppins, sans-serif",
  fontSize: 13,
  headerFontWeight: 600,
  wrapperBorderRadius: 18,
  borderRadius: 8,
});

const auditApi = agGrid.createGrid($("audit-grid"), {
  theme: auditTheme,
  columnDefs: auditColumns,
  defaultColDef: { sortable: true, sortingOrder: ["asc", "desc"], resizable: true, filter: true, floatingFilter: true, minWidth: 90 },
  rowData: [],
  getRowId: p => p.data.id == null ? "total" : String(p.data.id),
  domLayout: "autoHeight",
  pagination: true,
  paginationPageSize: 20,
  paginationPageSizeSelector: [20, 50, 100],
  rowSelection: { mode: "singleRow", checkboxes: false, enableClickSelection: true, enableSelectionWithoutKeys: true },
  enableCellTextSelection: true,
  ensureDomOrder: true,
  includeHiddenColumnsInQuickFilter: true,
  tooltipShowDelay: 300,
  localeText: { noRowsToShow: "No payments yet.", noMatchingRows: "No payments match this view." },
  isExternalFilterPresent: () => auditGroup !== "all",
  doesExternalFilterPass: node => inGroup[auditGroup](node.data.status),
  onSelectionChanged: showDetail,
  onFilterChanged: dropFilteredSelection,
  onModelUpdated: updateTotal,
});

// The bottom row adds up what was really taken, for whatever the filters currently show
function updateTotal(event) {
  let paid = 0;
  event.api.forEachNodeAfterFilter(n => { if (n.data && n.data.status === "paid") paid += n.data.amount; });
  event.api.setGridOption("pinnedBottomRowData", [{ item: "Total paid in this view", amount: Math.round(paid * 100) / 100 }]);
}

// Do not leave the details of a row on screen after a filter has hidden that row
function dropFilteredSelection(event) {
  const shown = new Set();
  event.api.forEachNodeAfterFilter(n => shown.add(n));
  event.api.getSelectedNodes().forEach(n => { if (!shown.has(n)) n.setSelected(false); });
}

function showDetail() {
  const box = $("audit-detail"), p = auditApi.getSelectedRows()[0];
  box.hidden = !p;
  if (!p) return;
  const fact = (label, value) => value ? `<dt>${label}</dt><dd>${esc(value)}</dd>` : "";
  setHTML(box, `<h3>#${p.id} ${esc(p.item)}, ${money(p.amount)}: ${esc(statusWord[p.status] || p.status)}</h3>
    <div class="why">${why(p)}</div>
    <dl class="audit-facts">
      ${fact("Task", p.task)}${fact("Agent", p.agent)}${fact("Store", p.merchant)}${fact("Page", p.product_url)}
      ${fact("PayPal order", p.order_id)}${fact("PayPal hold", p.authorization_id)}${fact("PayPal capture", p.capture_id)}${fact("PayPal refund", p.refund_id)}
      ${fact("PayPal reported", (p.paypal_events || []).map(e => e.label).join(", "))}
    </dl>`);
}

async function loadAudit() {
  const rows = await api("/audit");
  auditApi.setGridOption("rowData", rows);
  for (const group of Object.keys(inGroup)) $("c-" + group).textContent = rows.filter(p => inGroup[group](p.status)).length;
  showDetail();
}

// A spreadsheet runs a cell starting with one of these as a formula, and agents and shops choose some of this text
function safeForSpreadsheet(p) {
  if (p.value instanceof Date) {
    const d = p.value, two = n => String(n).padStart(2, "0");
    // Excel shows a real date in a narrow column as #####, so "at" keeps this as text. It still sorts in time order.
    return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} at ${two(d.getHours())}:${two(d.getMinutes())}`;
  }
  return typeof p.value === "string" && /^[=+\-@\t\r]/.test(p.value) ? "'" + p.value : p.value;
}

$("audit-chips").addEventListener("click", e => {
  const chip = e.target.closest("[data-filter]");
  if (!chip) return;
  auditGroup = chip.dataset.filter;
  document.querySelectorAll("#audit-chips .filter-chip").forEach(c => c.setAttribute("aria-pressed", c === chip));
  auditApi.onFilterChanged();
});
$("audit-search").addEventListener("input", e => auditApi.setGridOption("quickFilterText", e.target.value));
$("audit-ids").onclick = e => {
  idsShown = !idsShown;
  auditApi.setColumnsVisible(["order_id", "capture_id", "refund_id"], idsShown);
  e.currentTarget.setAttribute("aria-pressed", idsShown);
  e.currentTarget.textContent = idsShown ? "Hide PayPal IDs" : "Show PayPal IDs";
};
$("audit-csv").onclick = () => auditApi.exportDataAsCsv({
  fileName: "leash-audit-log.csv", allColumns: true, skipPinnedBottom: true, processCellCallback: safeForSpreadsheet,
});

$("week-btn").onclick = async e => {
  const btn = e.currentTarget, out = $("week-out");
  btn.disabled = true; btn.innerHTML = `<span class="spin"></span>Writing`;
  try {
    const r = await api("/summary", { method: "POST" });
    out.innerHTML = `<p class="week-note">${esc(r.summary)}</p>
      ${r.watch ? `<p class="week-watch">${esc(r.watch)}</p>` : ""}
      <ul class="week-agents">${r.agents.map(a => `<li><b>${esc(a.agent)}</b>
        <span>${money(a.paid_total)} paid in ${a.paid}, ${a.waiting} waiting, ${a.stopped} stopped, ${a.refunded} refunded</span></li>`).join("")}</ul>`;
    out.hidden = false;
  } catch (err) { toast("blocked", "Could not make the summary: " + err.message); }
  btn.disabled = false; btn.textContent = "Summarize my agents";
};

loadAudit().catch(() => {});
setInterval(() => { if (!document.hidden) loadAudit().catch(() => {}); }, AUDIT_POLL_MS);
