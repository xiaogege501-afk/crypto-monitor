const HOLDINGS_POLL_MS = 20000;
const RECOMMEND_POLL_MS = 2000;

function trendDotClass(text) {
  if (!text) return "na";
  if (text.indexOf("多头") >= 0) return "bull";
  if (text.indexOf("空头") >= 0) return "bear";
  return "na";
}

function fmtTime(ts) {
  if (!ts) return "未更新";
  const d = new Date(ts * 1000);
  return "更新于 " + d.toLocaleTimeString("zh-CN", { hour12: false });
}

function rowHtml(row, removable) {
  const tfs = [row.tf15, row.tf1h, row.tfd, row.tfw];
  const labels = ["15m", "1h", "D", "W"];
  const dots = tfs.map((t, i) => {
    const cls = trendDotClass(t);
    return `<span class="dot ${cls}" title="${labels[i]}: ${t}"></span>`;
  }).join("");

  const priceStr = row.price !== null && row.price !== undefined
    ? Number(row.price).toLocaleString("en-US", { maximumFractionDigits: 6 })
    : "-";

  let sigClass = "none", sigText = "-";
  if (row.signal_type === "buy") { sigClass = "buy"; sigText = "🟢 买入 · 四周期共振多头"; }
  else if (row.signal_type === "short") { sigClass = "short"; sigText = "🔴 做空 · 四周期共振空头"; }

  const rowCls = row.signal_type === "buy" ? "row-buy" : (row.signal_type === "short" ? "row-short" : "");

  const removeBtn = removable
    ? `<button class="remove-btn" data-symbol="${row.symbol}" title="从持仓移除">✕</button>`
    : "";

  return `
    <tr class="${rowCls}">
      <td class="sym">${row.symbol}</td>
      <td class="price">${priceStr}</td>
      <td colspan="4">
        <div class="dot-row">${dots}
          <span class="tf-label">${tfs[0]} · ${tfs[1]} · ${tfs[2]} · ${tfs[3]}</span>
        </div>
      </td>
      <td><span class="signal-badge ${sigClass}">${sigText}</span></td>
      <td>${removeBtn}</td>
    </tr>`;
}

function renderTable(tbodyId, results, removable) {
  const tbody = document.getElementById(tbodyId);
  if (!results || results.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty">暂无数据</td></tr>`;
    return;
  }
  tbody.innerHTML = results.map(r => rowHtml(r, removable)).join("");
  if (removable) {
    tbody.querySelectorAll(".remove-btn").forEach(btn => {
      btn.addEventListener("click", () => removeHolding(btn.dataset.symbol));
    });
  }
}

// ---------------- 持仓 ----------------
async function loadHoldings() {
  try {
    const res = await fetch("/api/holdings");
    const data = await res.json();
    renderTable("holdings-body", data.result, true);
    document.getElementById("holdings-meta").textContent =
      data.status === "running" ? "刷新中..." : fmtTime(data.updated_at);
  } catch (e) {
    document.getElementById("holdings-meta").textContent = "获取失败";
  }
}

async function addHolding() {
  const input = document.getElementById("add-symbol");
  const symbol = input.value.trim();
  if (!symbol) return;
  input.value = "";
  await fetch("/api/holdings/add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol }),
  });
  setTimeout(loadHoldings, 1500);
}

async function removeHolding(symbol) {
  await fetch("/api/holdings/remove", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol }),
  });
  loadHoldings();
}

async function forceRefreshHoldings() {
  document.getElementById("holdings-meta").textContent = "刷新中...";
  await fetch("/api/holdings/refresh", { method: "POST" });
  setTimeout(loadHoldings, 1500);
}

// ---------------- 推荐 ----------------
let recommendPollTimer = null;

async function scanRecommend() {
  const btn = document.getElementById("scan-btn");
  btn.disabled = true;
  document.getElementById("recommend-meta").textContent = "扫描中...";
  const res = await fetch("/api/recommend/scan", { method: "POST" });
  if (res.status === 409) {
    document.getElementById("recommend-meta").textContent = "已有任务在进行";
  }
  if (recommendPollTimer) clearInterval(recommendPollTimer);
  recommendPollTimer = setInterval(pollRecommendStatus, RECOMMEND_POLL_MS);
  pollRecommendStatus();
}

async function pollRecommendStatus() {
  try {
    const res = await fetch("/api/recommend/status");
    const data = await res.json();
    const btn = document.getElementById("scan-btn");
    if (data.status === "running") {
      document.getElementById("recommend-meta").textContent = data.log || "扫描中...";
    } else {
      btn.disabled = false;
      if (recommendPollTimer) { clearInterval(recommendPollTimer); recommendPollTimer = null; }
      document.getElementById("recommend-meta").textContent =
        data.status === "error" ? ("出错: " + data.error) : fmtTime(data.updated_at);
      if (data.result && data.result.length) {
        renderTable("recommend-body", data.result, false);
      }
    }
  } catch (e) {
    // ignore transient errors
  }
}

// ---------------- 设置 ----------------
async function loadSettings() {
  const res = await fetch("/api/config");
  const cfg = await res.json();
  document.getElementById("interval-input").value = cfg.auto_refresh_minutes ?? 5;
  document.getElementById("count-input").value = cfg.recommend_count ?? 10;
  document.getElementById("proxy-input").value = cfg.proxy ?? "";
}

async function saveSettings() {
  const body = {
    auto_refresh_minutes: document.getElementById("interval-input").value,
    recommend_count: document.getElementById("count-input").value,
    proxy: document.getElementById("proxy-input").value,
  };
  await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const btn = document.getElementById("save-settings-btn");
  const orig = btn.textContent;
  btn.textContent = "已保存";
  setTimeout(() => (btn.textContent = orig), 1200);
}

// ---------------- 时钟 ----------------
function tickClock() {
  document.getElementById("clock").textContent =
    new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

// ---------------- 初始化 ----------------
document.getElementById("add-btn").addEventListener("click", addHolding);
document.getElementById("add-symbol").addEventListener("keydown", (e) => {
  if (e.key === "Enter") addHolding();
});
document.getElementById("refresh-holdings-btn").addEventListener("click", forceRefreshHoldings);
document.getElementById("scan-btn").addEventListener("click", scanRecommend);
document.getElementById("save-settings-btn").addEventListener("click", saveSettings);

loadSettings();
loadHoldings();
setInterval(loadHoldings, HOLDINGS_POLL_MS);
setInterval(tickClock, 1000);
tickClock();
