/* ============================================================
   SpendScope – Chart initializers (Chart.js 4)
   ============================================================ */

Chart.defaults.color = "#9e9e9e";
Chart.defaults.borderColor = "rgba(255,255,255,0.06)";
Chart.defaults.font.family = "system-ui, sans-serif";
Chart.defaults.font.size = 11;

// Month names, category palette (app/parser.py) and labels are injected by base.html.
const MONTH_LABELS = window.MONTH_LABELS_SHORT;
const CATEGORY_COLORS = window.CATEGORY_COLORS;
const CHART_LABELS = window.CHART_LABELS;

// Locale-aware euro formatting, matching the server-side |eur filter.
const _eurFormatters = {};
function fmtEur(value, decimals = 2) {
  const key = String(decimals);
  if (!_eurFormatters[key]) {
    _eurFormatters[key] = new Intl.NumberFormat(window.APP_LANG === "en" ? "en-IE" : "de-DE", {
      style: "currency",
      currency: "EUR",
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  }
  return _eurFormatters[key].format(value);
}
const axisEur = v => fmtEur(v, 0);

// Year palette — distinct colours for multi-year charts
const YEAR_COLORS = [
  "#4CAF50", "#2196F3", "#FF9800", "#E91E63",
  "#9C27B0", "#00BCD4", "#FF5722", "#8BC34A",
];

// Keep chart instances so we can destroy before re-init
const _charts = {};

function _destroyChart(id) {
  if (_charts[id]) {
    _charts[id].destroy();
    delete _charts[id];
  }
}

/* ----------------------------------------------------------
   Trend chart — monthly spending vs. budget (line)
   data: [{label, total, budget, over_budget}, ...]
   ---------------------------------------------------------- */
function initTrendChart(canvasId, data) {
  _destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx || !data.length) return;

  const labels  = data.map(d => d.label);
  const totals  = data.map(d => d.total);
  const budgets = data.map(d => d.budget);

  _charts[canvasId] = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: CHART_LABELS.spending,
          data: totals,
          borderColor: "#4CAF50",
          backgroundColor: "rgba(76,175,80,0.1)",
          borderWidth: 2,
          pointRadius: 3,
          pointHoverRadius: 5,
          fill: true,
          tension: 0.3,
        },
        {
          label: CHART_LABELS.budget,
          data: budgets,
          borderColor: "rgba(255,152,0,0.5)",
          borderDash: [5, 4],
          borderWidth: 1.5,
          pointRadius: 0,
          fill: false,
          tension: 0,
        },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "top", labels: { boxWidth: 12 } },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: ${fmtEur(ctx.parsed.y)}`,
          },
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { callback: axisEur },
          grid: { color: "rgba(255,255,255,0.05)" },
        },
        x: {
          grid: { display: false },
          ticks: { maxRotation: 45 },
        },
      },
    },
  });
}

/* ----------------------------------------------------------
   Category chart — donut
   data: [{category, total, color}, ...]
   ---------------------------------------------------------- */
function initCategoryChart(canvasId, data) {
  _destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx || !data.length) return;

  _charts[canvasId] = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: data.map(d => d.category),
      datasets: [{
        data: data.map(d => d.total),
        backgroundColor: data.map(d => d.color || "#9e9e9e"),
        borderColor: "#111318",
        borderWidth: 2,
        hoverOffset: 6,
      }],
    },
    options: {
      responsive: true,
      cutout: "65%",
      plugins: {
        legend: {
          position: "bottom",
          labels: { boxWidth: 10, padding: 8 },
        },
        tooltip: {
          callbacks: {
            label: ctx => ` ${fmtEur(ctx.parsed)}`,
          },
        },
      },
    },
  });
}

/* ----------------------------------------------------------
   Store chart — horizontal bar
   data: [{display_store, total}, ...]
   ---------------------------------------------------------- */
function initStoreChart(canvasId, data) {
  _destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx || !data.length) return;

  const sorted = [...data].sort((a, b) => a.total - b.total);

  _charts[canvasId] = new Chart(ctx, {
    type: "bar",
    data: {
      labels: sorted.map(d => d.display_store),
      datasets: [{
        label: CHART_LABELS.spending,
        data: sorted.map(d => d.total),
        backgroundColor: "rgba(76,175,80,0.55)",
        borderColor: "#4CAF50",
        borderWidth: 1,
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: { label: ctx => ` ${fmtEur(ctx.parsed.x)}` },
        },
      },
      scales: {
        x: {
          beginAtZero: true,
          ticks: { callback: axisEur },
          grid: { color: "rgba(255,255,255,0.05)" },
        },
        y: { grid: { display: false } },
      },
    },
  });
}

/* ----------------------------------------------------------
   Year-over-Year grouped bar chart
   data: { years: [2022, 2023, ...], data: { 2022: [jan,...,dec], ... } }
   ---------------------------------------------------------- */
function initYoYChart(canvasId, data) {
  _destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx || !data.years || !data.years.length) return;

  const labels = MONTH_LABELS.slice(1); // Jan–Dez

  const datasets = data.years.map((year, i) => ({
    label: String(year),
    data: data.data[year],
    backgroundColor: (YEAR_COLORS[i] || "#9e9e9e") + "99",
    borderColor: YEAR_COLORS[i] || "#9e9e9e",
    borderWidth: 1,
    borderRadius: 3,
  }));

  _charts[canvasId] = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "top", labels: { boxWidth: 12 } },
        tooltip: {
          callbacks: { label: ctx => ` ${ctx.dataset.label}: ${fmtEur(ctx.parsed.y)}` },
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { callback: axisEur },
          grid: { color: "rgba(255,255,255,0.05)" },
        },
        x: { grid: { display: false } },
      },
    },
  });
}

/* ----------------------------------------------------------
   Category trends — stacked bar chart over time
   data: [{label, year, month, Lebensmittel: x, Tanken: y, ...}, ...]
   ---------------------------------------------------------- */
function initCategoryTrendChart(canvasId, data) {
  _destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx || !data.length) return;

  // Collect all category keys
  const catKeys = Object.keys(CATEGORY_COLORS);
  const labels = data.map(d => d.label);

  const datasets = catKeys
    .filter(cat => data.some(d => d[cat] > 0))
    .map(cat => ({
      label: cat,
      data: data.map(d => d[cat] || 0),
      backgroundColor: (CATEGORY_COLORS[cat] || "#9e9e9e") + "cc",
      borderColor: "transparent",
      borderWidth: 0,
      stack: "total",
    }));

  _charts[canvasId] = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "top",
          labels: { boxWidth: 10, padding: 8 },
        },
        tooltip: {
          callbacks: {
            label: ctx => ctx.parsed.y > 0
              ? ` ${ctx.dataset.label}: ${fmtEur(ctx.parsed.y)}`
              : null,
            footer: items => {
              const total = items.reduce((s, i) => s + i.parsed.y, 0);
              return `${CHART_LABELS.total}: ${fmtEur(total)}`;
            },
          },
          filter: item => item.parsed.y > 0,
        },
      },
      scales: {
        y: {
          stacked: true,
          beginAtZero: true,
          ticks: { callback: axisEur },
          grid: { color: "rgba(255,255,255,0.05)" },
        },
        x: {
          stacked: true,
          grid: { display: false },
          ticks: {
            maxRotation: 45,
            autoSkip: true,
            maxTicksLimit: 24,
          },
        },
      },
    },
  });
}
