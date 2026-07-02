const stateText = {
  healthy: "运行正常",
  attention: "需要检查",
};

let setupStep = 0;
let latestOverview = null;

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
  latestOverview = data;
  const storage = data.storage?.root || {};
  const network = data.network || {};
  const services = data.services || {};
  const health = data.health || {};

  text("[data-device-name]", data.system?.name || "LY-NAS");
  text("[data-admin-name]", data.setup?.admin_username || "Admin");
  text("[data-console-subtitle]", data.setup?.completed ? "控制台" : "等待初始化");
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
  renderSetupSummary(data);
}

function renderSetupSummary(data) {
  text("[data-setup-disks]", `${data.storage?.disk_count || 0} 块`);
  text("[data-setup-ip]", data.network?.primary_ip || "127.0.0.1");
  text("[data-setup-health]", data.health?.label || "准备就绪");
}

function updateSetupStep(nextStep) {
  setupStep = Math.max(0, Math.min(3, nextStep));

  document.querySelectorAll("[data-setup-page]").forEach((page) => {
    page.classList.toggle("active", Number(page.dataset.setupPage) === setupStep);
  });
  document.querySelectorAll("[data-step-indicator]").forEach((item) => {
    item.classList.toggle("active", Number(item.dataset.stepIndicator) === setupStep);
  });

  const prev = document.querySelector("[data-setup-prev]");
  const next = document.querySelector("[data-setup-next]");
  const finish = document.querySelector("[data-setup-finish]");
  if (prev) prev.hidden = setupStep === 0;
  if (next) next.hidden = setupStep === 3;
  if (finish) finish.hidden = setupStep !== 3;
}

function showSetupError(message) {
  const node = document.querySelector("[data-setup-error]");
  if (!node) return;
  node.textContent = message || "请检查初始化信息。";
  node.hidden = !message;
}

function setupPayload() {
  const form = document.querySelector("[data-setup-form]");
  const data = new FormData(form);
  return {
    device_name: String(data.get("device_name") || "").trim(),
    admin_username: String(data.get("admin_username") || "").trim(),
    admin_password: String(data.get("admin_password") || ""),
    network_mode: String(data.get("network_mode") || "dhcp"),
    enable_smb: data.get("enable_smb") === "on",
  };
}

function validateCurrentStep() {
  const payload = setupPayload();
  if (setupStep === 0 && !/^[A-Za-z0-9][A-Za-z0-9-]{1,30}[A-Za-z0-9]$/.test(payload.device_name)) {
    return "设备名需为 3-32 位字母、数字或短横线。";
  }
  if (setupStep === 1 && !/^[a-z_][a-z0-9_-]{2,31}$/.test(payload.admin_username)) {
    return "管理员名需为 3-32 位小写字母、数字、下划线或短横线。";
  }
  if (setupStep === 1 && payload.admin_password.length < 8) {
    return "管理员密码至少需要 8 位。";
  }
  return "";
}

async function completeSetup(event) {
  event.preventDefault();
  showSetupError("");

  try {
    const response = await fetch("/api/setup/complete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(setupPayload()),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      const firstError = result.errors ? Object.values(result.errors)[0] : result.error;
      throw new Error(firstError || "初始化失败。请稍后重试。");
    }

    document.querySelector("[data-setup-overlay]")?.setAttribute("hidden", "");
    await loadOverview();
  } catch (error) {
    showSetupError(error.message);
  }
}

async function loadSetupState() {
  try {
    const response = await fetch("/api/setup/state", { cache: "no-store" });
    if (!response.ok) throw new Error("setup api unavailable");
    const setup = await response.json();
    if (!setup.completed) {
      const overlay = document.querySelector("[data-setup-overlay]");
      if (overlay) overlay.hidden = false;
      updateSetupStep(0);
      renderSetupSummary(latestOverview || {});
    }
  } catch (error) {
    // Static preview keeps the console usable even when the API is not running.
  }
}

function bindSetupWizard() {
  document.querySelector("[data-setup-prev]")?.addEventListener("click", () => {
    showSetupError("");
    updateSetupStep(setupStep - 1);
  });
  document.querySelector("[data-setup-next]")?.addEventListener("click", () => {
    const error = validateCurrentStep();
    if (error) {
      showSetupError(error);
      return;
    }
    showSetupError("");
    updateSetupStep(setupStep + 1);
  });
  document.querySelector("[data-setup-form]")?.addEventListener("submit", completeSetup);
  updateSetupStep(0);
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

document.addEventListener("DOMContentLoaded", async () => {
  bindSetupWizard();
  await loadOverview();
  await loadSetupState();
});
