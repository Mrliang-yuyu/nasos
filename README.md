# Lingyue OS / 凌岳OS

凌岳OS 是一个面向 NAS 场景的可视化操作系统原型。当前仓库包含：

- 官网与 ISO 下载页原型：`index.html`
- Web 管理台原型：`console.html`
- Debian Live ISO 构建工程：`iso/`

## ISO 构建目标

首版 ISO 目标产物：

```text
dist/lyos-v0.1-alpha.iso
dist/lyos-v0.1-alpha.iso.sha256
```

当前 ISO 基于 Debian Bookworm live-build，启动后会运行 Nginx，并将凌岳OS官网与控制台原型放在：

```text
/opt/lingyue/www
```

同时会启动本机 API 服务，用于向控制台提供系统状态、磁盘、网络和核心服务信息：

```text
http://127.0.0.1:8088/api/system/overview
```

系统启动后可通过浏览器访问设备 IP：

```text
http://<device-ip>/
```

## 构建 ISO

### 在 macOS 上构建

推荐使用 Docker Desktop 构建。Apple Silicon 设备会通过 `linux/amd64` 容器生成 amd64 ISO：

```bash
./iso/build-with-docker.sh
```

构建完成后，ISO 会输出到 `dist/`。

### 在 GitHub 上构建

仓库内置了 GitHub Actions 工作流：`Build Lingyue OS ISO`。

进入 GitHub 仓库后打开 `Actions`，选择 `Build Lingyue OS ISO`，点击 `Run workflow`。构建完成后可在本次运行的 Artifacts 中下载：

```text
lingyue-os-alpha-iso
```

## 注意事项

- 当前是 Alpha 原型 ISO，不适合存放真实数据。
- 当前 Web 控制台是静态原型，尚未接入真实后端。
- ISO 构建依赖 Linux live-build 环境，因此 macOS 通过 Docker 容器构建。
