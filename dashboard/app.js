const DATA_CANDIDATES = [
  "../data/dashboard.json",
  "./data/dashboard.json",
  "/data/dashboard.json",
];

const state = {
  payload: null,
  selectedKey: null,
  query: "",
  sort: "title",
  range: "all",
  chart: null,
};

function $(id) {
  return document.getElementById(id);
}

function formatPrice(value, currency) {
  if (value == null || Number.isNaN(value)) return "—";
  const code = currency || "USD";
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: code,
    }).format(value);
  } catch {
    return `$${Number(value).toFixed(2)}`;
  }
}

function formatDate(iso) {
  if (!iso) return "—";
  const date = new Date(`${iso}T00:00:00Z`);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

function deltaClass(change) {
  if (change == null || change === 0) return "flat";
  return change < 0 ? "drop" : "rise";
}

function formatDelta(item) {
  if (item.change == null) return "No change yet";
  const sign = item.change > 0 ? "+" : "";
  const pct = item.changePct == null ? "" : ` (${sign}${item.changePct.toFixed(2)}%)`;
  return `${sign}${formatPrice(item.change, item.currency)}${pct}`;
}

function historyInRange(item) {
  if (state.range === "all") return item.history;
  const days = Number(state.range);
  const latest = state.payload.lastDate || item.history.at(-1)?.date;
  if (!latest) return item.history;
  const cutoff = new Date(`${latest}T00:00:00Z`);
  cutoff.setUTCDate(cutoff.getUTCDate() - days);
  const cutoffIso = cutoff.toISOString().slice(0, 10);
  return item.history.filter((point) => point.date >= cutoffIso);
}

function sparkline(item) {
  const points = historyInRange(item).filter((point) => point.price != null);
  const width = 220;
  const height = 28;
  if (points.length === 0) {
    return `<svg class="spark" viewBox="0 0 ${width} ${height}" aria-hidden="true"></svg>`;
  }
  if (points.length === 1) {
    return `<svg class="spark" viewBox="0 0 ${width} ${height}" aria-hidden="true">
      <circle cx="${width / 2}" cy="${height / 2}" r="3" fill="currentColor" />
    </svg>`;
  }
  const prices = points.map((point) => point.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = max - min || 1;
  const coords = points
    .map((point, index) => {
      const x = (index / (points.length - 1)) * width;
      const y = height - 3 - ((point.price - min) / range) * (height - 6);
      return `${x},${y}`;
    })
    .join(" ");
  const color = prices.at(-1) <= prices[0] ? "var(--drop)" : "var(--rise)";
  return `<svg class="spark" viewBox="0 0 ${width} ${height}" aria-hidden="true">
    <polyline fill="none" stroke="${color}" stroke-width="2" points="${coords}" />
  </svg>`;
}

function filteredItems() {
  const query = state.query.trim().toLowerCase();
  let items = state.payload.items.slice();
  if (query) {
    items = items.filter((item) =>
      `${item.title} ${item.asin} ${item.key}`.toLowerCase().includes(query)
    );
  }
  const price = (item) => item.currentPrice ?? -Infinity;
  const change = (item) => item.changePct ?? 0;
  items.sort((a, b) => {
    switch (state.sort) {
      case "price-asc":
        return price(a) - price(b);
      case "price-desc":
        return price(b) - price(a);
      case "drop":
        return change(a) - change(b);
      case "rise":
        return change(b) - change(a);
      default:
        return a.title.localeCompare(b.title);
    }
  });
  return items;
}

function renderStats() {
  const { payload } = state;
  $("stats").innerHTML = `
    <div><dt>Items</dt><dd>${payload.itemCount}</dd></div>
    <div><dt>Available</dt><dd>${payload.availableCount}</dd></div>
    <div><dt>Last recorded</dt><dd>${formatDate(payload.lastDate)}</dd></div>
  `;
  const listBit = payload.listUrl
    ? ` <a href="${payload.listUrl}" target="_blank" rel="noopener noreferrer">Open the Amazon list</a>.`
    : "";
  $("lede").innerHTML = `Daily prices from the tracker’s CSV history. The line fills in as GitHub Actions records more days.${listBit}`;
}

function renderList() {
  const items = filteredItems();
  const list = $("list");
  if (!items.length) {
    list.innerHTML = `<p class="chart-meta">No items match that search.</p>`;
    return;
  }
  list.innerHTML = items
    .map((item) => {
      const selected = item.key === state.selectedKey;
      const priceClass = item.available ? "" : "unavailable";
      const changeText =
        item.change == null ? "1 day of history" : formatDelta(item);
      return `<button type="button" class="item" data-key="${item.key}" aria-selected="${selected}">
        <span class="item-title">${escapeHtml(shortTitle(item.title))}</span>
        <span class="item-price ${priceClass}">${item.available ? formatPrice(item.currentPrice, item.currency) : "Unavailable"}</span>
        <span class="item-change ${deltaClass(item.change)}">${escapeHtml(changeText)}</span>
        ${sparkline(item)}
      </button>`;
    })
    .join("");
  list.querySelectorAll(".item").forEach((button) => {
    button.addEventListener("click", () => selectItem(button.dataset.key));
  });
}

function shortTitle(title) {
  const cut = title.split("|")[0].split(",")[0].trim();
  return cut.length > 72 ? `${cut.slice(0, 69)}…` : cut;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function selectItem(key, pushHash = true) {
  state.selectedKey = key;
  if (pushHash) {
    const url = new URL(window.location.href);
    url.hash = key;
    history.replaceState(null, "", url);
  }
  renderList();
  renderChart();
}

function renderChart() {
  const item = state.payload.items.find((entry) => entry.key === state.selectedKey);
  const empty = $("chart-empty");
  const note = $("chart-note");
  const canvas = $("price-chart");
  const amazon = $("amazon-link");
  const priceRow = $("price-row");
  const stats = $("chart-stats");

  if (!item) {
    $("chart-title").textContent = "Select an item";
    $("chart-meta").textContent = "";
    amazon.hidden = true;
    priceRow.hidden = true;
    stats.innerHTML = "";
    empty.hidden = false;
    empty.textContent = "Choose an item on the left to plot its recorded prices.";
    note.hidden = true;
    if (state.chart) {
      state.chart.destroy();
      state.chart = null;
    }
    return;
  }

  const series = historyInRange(item);
  $("chart-title").textContent = item.title;
  $("chart-meta").textContent = `${item.asin || item.key} · ${series.length} recorded day${series.length === 1 ? "" : "s"}`;
  if (item.url) {
    amazon.hidden = false;
    amazon.href = item.url;
  } else {
    amazon.hidden = true;
  }
  priceRow.hidden = false;
  $("current-price").textContent = item.available
    ? formatPrice(item.currentPrice, item.currency)
    : "Unavailable";
  const delta = $("delta");
  delta.textContent = formatDelta(item);
  delta.className = `delta ${deltaClass(item.change)}`;

  const priced = series.filter((point) => point.price != null).map((point) => point.price);
  stats.innerHTML = `
    <div><dt>Low</dt><dd>${formatPrice(priced.length ? Math.min(...priced) : null, item.currency)}</dd></div>
    <div><dt>High</dt><dd>${formatPrice(priced.length ? Math.max(...priced) : null, item.currency)}</dd></div>
    <div><dt>First recorded</dt><dd>${formatPrice(item.firstPrice, item.currency)}</dd></div>
  `;

  if (typeof Chart === "undefined") {
    empty.hidden = false;
    note.hidden = true;
    empty.textContent = "Chart.js failed to load, so the table of prices is still available in the item list.";
    return;
  }

  if (!series.length || priced.length === 0) {
    empty.hidden = false;
    note.hidden = true;
    empty.textContent = "No priced days in this range yet.";
    if (state.chart) {
      state.chart.destroy();
      state.chart = null;
    }
    return;
  }

  empty.hidden = true;
  note.hidden = series.length > 1;
  note.textContent =
    "Only one day of history so far. The line will fill in after the next daily run records another price.";

  const labels = series.map((point) => point.date);
  const data = series.map((point) => point.price);
  let stroke = "#214e4a";
  let fill = "rgba(33, 78, 74, 0.10)";
  if (item.change != null) {
    const dropped = item.change <= 0;
    stroke = dropped ? "#1b7a4e" : "#b42318";
    fill = dropped ? "rgba(27, 122, 78, 0.12)" : "rgba(180, 35, 24, 0.10)";
  }

  if (state.chart) state.chart.destroy();
  state.chart = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          data,
          borderColor: stroke,
          backgroundColor: fill,
          fill: true,
          spanGaps: false,
          tension: 0.2,
          pointRadius: labels.length < 12 ? 4 : 2,
          pointHoverRadius: 6,
          borderWidth: 2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => (items[0] ? formatDate(items[0].label) : ""),
            label: (ctx) =>
              ctx.parsed.y == null ? "Unavailable" : formatPrice(ctx.parsed.y, item.currency),
          },
        },
      },
      scales: {
        x: {
          ticks: {
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 8,
            callback: (value) => formatDate(labels[value]),
          },
          grid: { display: false },
        },
        y: {
          grace: "12%",
          ticks: {
            callback: (value) => formatPrice(value, item.currency),
          },
        },
      },
    },
  });
}

async function loadPayload() {
  const errors = [];
  for (const url of DATA_CANDIDATES) {
    try {
      const response = await fetch(url);
      if (!response.ok) {
        errors.push(`${url} (${response.status})`);
        continue;
      }
      return await response.json();
    } catch (err) {
      errors.push(`${url} (${err.message})`);
    }
  }
  throw new Error(
    `Could not load price history. Serve the repo with python -m price_tracker dashboard. Tried: ${errors.join(", ")}`
  );
}

function bindControls() {
  $("search").addEventListener("input", (event) => {
    state.query = event.target.value;
    renderList();
  });
  $("sort").addEventListener("change", (event) => {
    state.sort = event.target.value;
    renderList();
  });
  $("range").addEventListener("change", (event) => {
    state.range = event.target.value;
    renderList();
    renderChart();
  });
  window.addEventListener("hashchange", () => {
    const key = decodeURIComponent(location.hash.replace(/^#/, ""));
    if (key && state.payload.items.some((item) => item.key === key)) {
      selectItem(key, false);
    }
  });
}

async function init() {
  bindControls();
  const status = $("status");
  try {
    state.payload = await loadPayload();
  } catch (err) {
    status.hidden = false;
    status.textContent = err.message;
    $("lede").textContent = "Price history could not be loaded.";
    return;
  }

  if (!state.payload.items.length) {
    status.hidden = false;
    status.textContent = "No price history recorded yet. Run python -m price_tracker run first.";
    return;
  }

  renderStats();
  const hashKey = decodeURIComponent(location.hash.replace(/^#/, ""));
  const initial =
    state.payload.items.find((item) => item.key === hashKey) ||
    filteredItems()[0];
  selectItem(initial.key);
}

init();
