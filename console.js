const stateText = {
  healthy: "运行正常",
  attention: "需要检查",
};

function text(selector, value) {
  const node = document.querySelector(selector);
  if (node && value !== undefined && value !== null) {
    node.textContent = value;
  }
}

function setUsage(percent) {
  const bar = document.querySelector("[data-usage-bar]");
  if (bar) {
    bar.style.width = `${Math.max(0, Math.min(100, percent || 0))}%`;
  }
}

function renderDisks(disks) {
  const grid = document.querySelector("[data-disk-grid]");
  if (!grid || !Array.isArray(disks)) {
    return;
  }

  if (!disks.length) {
    grid.innerHTML = '<div class="disk-card"><span>未发现磁盘</span><strong>0 B</strong><small>等待系统扫描</small></div>';
    return;
  }

  grid.innerHTML = disks.slice(0, 6).map((disk, index) => `
    <div class="disk-card">
      <span>Disk ${index + 1} · ${disk.name}</span>
      <strong>${disk.size_label}</strong>
      <small>${disk.model} · ${disk.transport}</small>
    </div>
  `).join("");
}

function renderServices(services) {
  const node = document.querySelector("[data-protocol-list]");
  if (!node || !services?.items) {
    return;
  }

  node.innerHTML = services.items.map((item) => `
    <div>
      <span>${item.label}</span>
      <strong class="${item.active ? "ok" : "muted-status"}">${item.active ? "已启用" : "未运行"}</strong>
    </div>
  `).join("");
}

function renderOverview(data) {
  const storage = data.storage?.root || {};
  const network = data.network || {};
  const services = data.services || {};
  const health = data.health || {};

  text("[data-device-name]", data.system?.name || "LY-NAS");
  text("[data-sidebar-health]", health.label || stateText[health.state] || "运行正常");
  text("[data-hero-title]", `你的私有数据中心${health.label || "运行正常"}`);
  text("[data-hero-copy]", `${data.storage?.disk_count || 0} 块磁盘在线，${services.active_count || 0} / ${services.total || 0} 个核心服务正在运行。系统已连续运行 ${data.system?.uptime || "未知"}。`);

  text("[data-storage-total]", storage.total_label || "--");
  text("[data-storage-used]", `已用 ${storage.used_label || "--"}`);
  text("[data-storage-free]", `剩余 ${storage.free_label || "--"}`);
  setUsage(storage.percent || 0);

  text("[data-disk-health]", data.storage?.disk_health || "0 / 0");
  text("[data-disk-health-copy]", data.storage?.disk_count ? "已完成系统扫描" : "未发现独立磁盘");
  text("[data-network-speed]", network.interfaces?.[0]?.speed || "未知速率");
  text("[data-network-ip]", network.primary_ip || "127.0.0.1");
  text("[data-service-main]", `${services.active_count || 0} / ${services.total || 0}`);
  text("[data-service-copy]", "核心服务运行中");

  renderDisks(data.storage?.disks || []);
  renderServices(services);
}

async function loadOverview() {
  try {
    const response = await fetch("/api/system/overview", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`API returned ${response.status}`);
    }
    renderOverview(await response.json());
  } catch (error) {
    text("[data-sidebar-health]", "原型预览");
    text("[data-hero-copy]", "当前没有连接凌岳OS 后端服务，页面正在显示内置演示数据。进入 ISO 环境后会自动读取真实系统状态。");
  }
}

document.addEventListener("DOMContentLoaded", loadOverview);
