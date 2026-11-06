// Audit log table. Cell values go to AG Grid as plain text and no column uses an HTML renderer,
// so an item name written by an agent or a shop cannot run as code on this page.
const AUDIT_POLL_MS = 5000;
const stopped = ["blocked", "cancelled"];
const inGroup = { all: () => true, paid: s => s === "paid", waiting: s => s === "waiting", stopped: s => stopped.includes(s) };
let auditGroup = "all", idsShown = false;

const toDate = text => text ? new Date(text.replace(" ", "T") + "Z") : null;
const moneyOrBlank = p => p.value == null ? "" : money(p.value);

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
const readableReasons = p => (p.reasons || []).map(r => r.replace(/^AI( ok)?:\s*/, "")).join(" ");

const auditColumns = [
  { field: "id", headerName: "#", width: 80, minWidth: 70, filter: "agNumberColumnFilter", sort: "desc" },
  { colId: "when", headerName: "When (your time)", width: 170, filter: "agDateColumnFilter",
    valueGetter: p => toDate(p.data.created_at),
    valueFormatter: p => p.value ? p.value.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "" },
  { field: "agent", headerName: "Agent", width: 130 },
  { field: "item", headerName: "Item", width: 230, tooltipField: "item" },
  { field: "amount", headerName: "Amount", width: 105, type: "rightAligned", filter: "agNumberColumnFilter", valueFormatter: moneyOrBlank },
  { colId: "result", headerName: "Result", width: 140,
    valueGetter: p => p.data.status ? (statusWord[p.data.status] || p.data.status) : "",
    cellClassRules: {
      "cell-paid": p => p.data.status === "paid",
      "cell-waiting": p => p.data.status === "waiting",
      "cell-stopped": p => stopped.includes(p.data.status),
    } },
  { colId: "decided", headerName: "Decided by", width: 125, valueGetter: p => decidedBy(p.data) },
  { colId: "reasons", headerName: "Reasons", flex: 1, minWidth: 300, valueGetter: p => readableReasons(p.data), tooltipValueGetter: p => p.value },
  { field: "merchant", headerName: "Store", width: 130 },
  { field: "category", headerName: "Category", width: 120 },
  { field: "quoted_price", headerName: "Quoted", width: 105, type: "rightAligned", filter: "agNumberColumnFilter", valueFormatter: moneyOrBlank },
  { colId: "change", headerName: "Price change", width: 125, type: "rightAligned", filter: "agNumberColumnFilter",
    valueGetter: p => priceChange(p.data),
    valueFormatter: p => p.value == null ? "" : (p.value > 0 ? "+" : "-") + money(Math.abs(p.value)),
    cellClassRules: { "cell-stopped": p => p.value > 0 } },
  { colId: "page", headerName: "Page read by Leash", width: 165,
    valueGetter: p => p.data.status ? (p.data.page_verified ? "Yes" : "No") : "" },
  { field: "order_id", headerName: "PayPal order", width: 190, hide: true },
  { field: "capture_id", headerName: "PayPal capture", width: 190, hide: true },
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
  defaultColDef: { sortable: true, resizable: true, filter: true, floatingFilter: true, minWidth: 90 },
  rowData: [],
  getRowId: p => p.data.id == null ? "total" : String(p.data.id),
  domLayout: "autoHeight",
  pagination: true,
  paginationPageSize: 20,
  paginationPageSizeSelector: [20, 50, 100],
  rowSelection: { mode: "singleRow", checkboxes: false, enableClickSelection: true },
  enableCellTextSelection: true,
  ensureDomOrder: true,
  includeHiddenColumnsInQuickFilter: true,
  tooltipShowDelay: 300,
  overlayNoRowsTemplate: "<span>No payments match this view.</span>",
  isExternalFilterPresent: () => auditGroup !== "all",
  doesExternalFilterPass: node => inGroup[auditGroup](node.data.status),
  onSelectionChanged: showDetail,
  onModelUpdated: updateTotal,
});

// The bottom row adds up what was really taken, for whatever the filters currently show
function updateTotal(event) {
  let paid = 0;
  event.api.forEachNodeAfterFilter(n => { if (n.data && n.data.status === "paid") paid += n.data.amount; });
  event.api.setGridOption("pinnedBottomRowData", [{ item: "Total paid in this view", amount: Math.round(paid * 100) / 100 }]);
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
      ${fact("PayPal order", p.order_id)}${fact("PayPal hold", p.authorization_id)}${fact("PayPal capture", p.capture_id)}
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
  if (p.value instanceof Date) return p.value.toISOString();
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
  auditApi.setColumnsVisible(["order_id", "capture_id"], idsShown);
  e.currentTarget.setAttribute("aria-pressed", idsShown);
  e.currentTarget.textContent = idsShown ? "Hide PayPal IDs" : "Show PayPal IDs";
};
$("audit-csv").onclick = () => auditApi.exportDataAsCsv({
  fileName: "leash-audit-log.csv", allColumns: true, skipPinnedBottom: true, processCellCallback: safeForSpreadsheet,
});

loadAudit().catch(() => {});
setInterval(() => { if (!document.hidden) loadAudit().catch(() => {}); }, AUDIT_POLL_MS);
