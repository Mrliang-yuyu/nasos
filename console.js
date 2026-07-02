const stateText = {
  healthy: "运行正常",
  attention: "需要检查",
};

let setupStep = 0;
let latestOverview = null;
let latestStorage = null;
let latestShares = null;
let latestInstall = null;

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

function showActionNote(selector, message, isError = false) {
  const node = document.querySelector(selector);
  if (!node) return;
  node.textContent = message || "";
  node.hidden = !message;
  node.classList.toggle("error", isError);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  }[char]));
}

function diskRoleLabel(role) {
  return {
    system: "系统盘",
    mounted: "已挂载",
    available: "可规划",
  }[role] || "未知";
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
    <div class="disk-card ${disk.pool_candidate ? "candidate" : ""}">
      <span>Disk ${index + 1} · ${escapeHtml(disk.name)}</span>
      <strong>${escapeHtml(disk.size_label)}</strong>
      <small>${escapeHtml(disk.model)} · ${escapeHtml(disk.transport)} · ${diskRoleLabel(disk.role)}</small>
    </div>
  `).join("");
}

function renderPoolList(pools) {
  const node = document.querySelector("[data-pool-list]");
  if (!node || !Array.isArray(pools)) return;

  if (!pools.length) {
    node.innerHTML = '<div class="table-row"><span>未创建</span><span>等待规划</span><span>--</span><span>待处理</span></div>';
    return;
  }

  node.innerHTML = pools.map((pool) => `
    <div class="table-row">
      <span>${escapeHtml(pool.name)}</span>
      <span>${escapeHtml(pool.mode_label || pool.mode)}</span>
      <span>${escapeHtml(pool.capacity_label || "--")}</span>
      <span class="planned">${escapeHtml(pool.status_label || "待执行")}</span>
    </div>
  `).join("");
}

function renderStorageOverview(data) {
  latestStorage = data;
  renderDisks(data.disks || []);
  renderPoolList(data.pools || []);

  text("[data-storage-disk-total]", `${data.summary?.total || 0} 块`);
  text("[data-storage-disk-available]", `${data.summary?.available || 0} 块`);
  text("[data-storage-recommendation]", data.recommendation?.label || "等待磁盘");
  text("[data-storage-recommendation-capacity]", data.recommendation?.capacity_label || "--");

  const badge = document.querySelector("[data-storage-scan-badge]");
  if (badge) {
    badge.textContent = data.summary?.available ? "发现可规划磁盘" : "只读扫描完成";
    badge.classList.toggle("ok-badge", Boolean(data.summary?.available));
  }
}

function renderInstallOverview(data) {
  latestInstall = data;
  text("[data-install-status]", data.ready_label || "等待扫描");
  text("[data-install-targets]", `${data.candidate_count || 0} 块`);
  text("[data-install-minimum]", data.minimum_size_label || "16 GB");
  text("[data-install-mode]", data.execution_enabled ? "可执行" : "安全模式");

  const target = document.querySelector("[data-install-target]");
  if (target) {
    const targets = data.targets || [];
    target.innerHTML = targets.length
      ? targets.map((disk) => `<option value="${escapeHtml(disk.name)}">${escapeHtml(disk.path)} · ${escapeHtml(disk.size_label)} · ${escapeHtml(disk.model)}</option>`).join("")
      : '<option value="">未发现可安装磁盘</option>';
    target.disabled = !targets.length;
  }

  const steps = document.querySelector("[data-install-steps]");
  const planSteps = data.latest_plan?.steps || ["扫描空闲磁盘", "生成安装计划", "等待执行安装器"];
  if (steps) {
    steps.innerHTML = planSteps.map((step, index) => `<li><span>${index + 1}</span>${escapeHtml(step)}</li>`).join("");
  }

  const events = document.querySelector("[data-install-events]");
  if (events) {
    const items = (data.events || []).slice(-5).reverse();
    events.innerHTML = items.length
      ? items.map((item) => `<li><span>${escapeHtml(item.level)}</span>${escapeHtml(item.message)}</li>`).join("")
      : '<li><span>idle</span>等待安装计划</li>';
  }
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

function renderSharesOverview(data) {
  latestShares = data;
  const list = document.querySelector("[data-share-list]");
  if (list) {
    const shares = data.shares || [];
    list.innerHTML = shares.map((share) => `
      <div>
        <strong>${escapeHtml(share.name)}</strong>
        <span>${escapeHtml(share.protocol || "SMB")} · ${escapeHtml(share.access_label || "认证用户读写")} · ${escapeHtml(share.status_label || "待创建")}</span>
      </div>
    `).join("") || '<div><strong>Public</strong><span>SMB · 认证用户读写 · 等待创建</span></div>';
  }

  const protocolList = document.querySelector("[data-protocol-list]");
  if (protocolList && data.samba) {
    protocolList.innerHTML = `
      <div><span>SMB</span><strong class="${data.samba.active ? "ok" : "planned"}">${data.samba.active ? "已运行" : data.samba.installed ? "已安装" : "未安装"}</strong></div>
      <div><span>NFS</span><strong class="muted-status">待接入</strong></div>
      <div><span>WebDAV</span><strong class="muted-status">待接入</strong></div>
    `;
  }

  renderAccountOverview(data.accounts || {});
}

function renderAccountOverview(accounts) {
  const node = document.querySelector("[data-account-list]");
  if (!node) return;

  const configured = Boolean(accounts.configured);
  const preview = Boolean(accounts.preview);
  const username = accounts.admin_username || "admin";
  const group = accounts.group || "lingyue-users";
  const statusText = configured ? (preview ? "预览已记录" : "已接入 SMB") : "等待初始化";

  node.innerHTML = `
    <div><span>管理员</span><strong>${escapeHtml(username)}</strong></div>
    <div><span>共享用户组</span><strong>${escapeHtml(group)}</strong></div>
    <div><span>账号状态</span><strong class="${configured ? "ok" : "planned"}">${statusText}</strong></div>
  `;
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
    await loadSharesOverview();
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

async function loadStorageOverview() {
  try {
    const response = await fetch("/api/storage/overview", { cache: "no-store" });
    if (!response.ok) throw new Error(`Storage API returned ${response.status}`);
    renderStorageOverview(await response.json());
    showActionNote("[data-storage-action-note]", "");
  } catch (error) {
    showActionNote("[data-storage-action-note]", "当前没有连接存储 API，ISO 环境中会显示真实磁盘扫描结果。", true);
  }
}

async function loadInstallOverview() {
  try {
    const response = await fetch("/api/install/overview", { cache: "no-store" });
    if (!response.ok) throw new Error(`Install API returned ${response.status}`);
    renderInstallOverview(await response.json());
    showActionNote("[data-install-action-note]", "");
  } catch (error) {
    showActionNote("[data-install-action-note]", "当前没有连接安装器 API，ISO 环境中会显示真实安装预检。", true);
  }
}

async function loadSharesOverview() {
  try {
    const response = await fetch("/api/shares/overview", { cache: "no-store" });
    if (!response.ok) throw new Error(`Shares API returned ${response.status}`);
    renderSharesOverview(await response.json());
    showActionNote("[data-share-action-note]", "");
  } catch (error) {
    showActionNote("[data-share-action-note]", "当前没有连接共享 API，ISO 环境中会显示真实 SMB 状态。", true);
  }
}

async function createPoolPlan() {
  const recommendation = latestStorage?.recommendation;
  if (!recommendation || recommendation.mode === "none") {
    showActionNote("[data-storage-action-note]", recommendation?.message || "未发现可用于规划的空闲磁盘。", true);
    return;
  }

  try {
    const response = await fetch("/api/storage/pools/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "MainPool",
        mode: recommendation.mode,
        disk_names: recommendation.disk_names,
      }),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      const firstError = result.errors ? Object.values(result.errors)[0] : result.error;
      throw new Error(firstError || "创建规划失败。");
    }

    showActionNote("[data-storage-action-note]", "已生成存储池规划。当前版本不会格式化磁盘，后续会加入二次确认和执行日志。");
    await loadStorageOverview();
  } catch (error) {
    showActionNote("[data-storage-action-note]", error.message, true);
  }
}

function bindStorageActions() {
  document.querySelector("[data-refresh-storage]")?.addEventListener("click", loadStorageOverview);
  document.querySelector("[data-create-pool]")?.addEventListener("click", createPoolPlan);
}

async function createInstallPlan() {
  const target = document.querySelector("[data-install-target]")?.value || "";
  if (!target) {
    showActionNote("[data-install-action-note]", "未发现可用于安装的空闲磁盘。", true);
    return;
  }

  try {
    const response = await fetch("/api/install/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target }),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      const firstError = result.errors ? Object.values(result.errors)[0] : result.error;
      throw new Error(firstError || "安装计划生成失败。");
    }

    showActionNote("[data-install-action-note]", `已生成安装计划，目标：${result.plan.target}。当前不会写入磁盘。`);
    await loadInstallOverview();
  } catch (error) {
    showActionNote("[data-install-action-note]", error.message, true);
  }
}

async function executeInstallPlan() {
  try {
    const response = await fetch("/api/install/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: "INSTALL-LINGYUE" }),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      const firstError = result.errors ? Object.values(result.errors)[0] : result.error;
      throw new Error(firstError || "安装执行失败。");
    }

    showActionNote("[data-install-action-note]", result.run.message || result.run.status_label, result.run.status !== "completed");
    await loadInstallOverview();
  } catch (error) {
    showActionNote("[data-install-action-note]", error.message, true);
  }
}

function bindInstallActions() {
  document.querySelector("[data-refresh-install]")?.addEventListener("click", loadInstallOverview);
  document.querySelector("[data-create-install-plan]")?.addEventListener("click", createInstallPlan);
  document.querySelector("[data-execute-install]")?.addEventListener("click", executeInstallPlan);
}

async function createPublicShare() {
  try {
    const response = await fetch("/api/shares/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Public", access: "authenticated_rw" }),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      const firstError = result.errors ? Object.values(result.errors)[0] : result.error;
      throw new Error(firstError || "共享创建失败。");
    }

    const warning = result.share?.service_warning ? ` ${result.share.service_warning}` : "";
    showActionNote("[data-share-action-note]", `Public 认证共享已创建，使用初始化管理员账号访问。路径：${result.share.path}。${warning}`.trim(), Boolean(warning));
    await loadSharesOverview();
    await loadOverview();
  } catch (error) {
    showActionNote("[data-share-action-note]", error.message, true);
  }
}

function bindShareActions() {
  document.querySelector("[data-create-share]")?.addEventListener("click", createPublicShare);
  document.querySelector("[data-refresh-shares]")?.addEventListener("click", loadSharesOverview);
}

document.addEventListener("DOMContentLoaded", async () => {
  bindSetupWizard();
  bindStorageActions();
  bindInstallActions();
  bindShareActions();
  await loadOverview();
  await loadStorageOverview();
  await loadInstallOverview();
  await loadSharesOverview();
  await loadSetupState();
});
