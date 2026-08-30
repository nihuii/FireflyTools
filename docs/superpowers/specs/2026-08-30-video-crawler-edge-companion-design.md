# 视频爬取 Edge 辅助捕获设计规格

**日期：** 2026-08-30
**状态：** 已确认方向，等待书面规格复核
**适用项目：** FireflyTools 视频爬取功能

## 1. 背景与根因

目标站点在普通 Microsoft Edge 中能够完成人工验证并进入播放器，但由
Playwright 启动或控制的 Chromium、Google Chrome 会持续进入安全验证循环。
继续调整等待时间、浏览器 channel 或持久 profile 不能改变受自动化控制这一
根本差异。

本方案不再让 FireflyTools 自动访问受保护页面。页面导航、安全验证和点击播放
全部由用户在日常 Edge 中完成；扩展只在用户主动授权的当前标签页中观察已经
发生的媒体请求，并把用户选择的候选发送给本地 FireflyTools。

## 2. 目标与成功标准

### 2.1 目标

- 在不使用 Playwright 访问目标页面的情况下发现 HLS、MP4 和 DASH 媒体请求。
- 复用 FireflyTools 现有的候选验证、HLS 分片、断点恢复、合并和错误报告能力。
- 用户必须主动开始捕获、主动选择候选、主动确认导入，禁止后台长期监听。
- 所有数据只在本机 Edge、扩展和 FireflyTools 之间传递。

### 2.2 成功标准

- 用户在 Edge 中通过验证并播放后，扩展能在 10 秒内显示新出现的媒体候选。
- 扩展可区分 `HLS`、`DIRECT_MP4` 和 `DASH`，并按 URL 与类型去重。
- 用户选择候选后，FireflyTools 能收到有效的 HTTP(S) URL 和安全请求头快照。
- 默认流程不读取、不发送、不记录 Cookie 或 Authorization。
- FireflyTools 未运行、连接组件未安装、权限不足或候选过期时均给出明确中文提示。
- 停止捕获、关闭标签页或超过五分钟后，扩展自动清空该标签页的临时候选。

## 3. 范围与非目标

### 3.1 本期范围

- Microsoft Edge Stable，Manifest V3 扩展。
- Windows 当前用户安装方式。
- 只读观察用户选择的当前标签页。
- HLS、MP4、DASH 候选识别与发送。
- Native Messaging 本地通信。
- Native Messaging 不可用时的“复制候选 JSON”人工兜底。
- FireflyTools 视频界面的 Edge 连接状态、候选确认和导入入口。

### 3.2 非目标

- 不自动点击验证码、播放器或网页控件。
- 不隐藏 `navigator.webdriver`，不注入 stealth 或修改浏览器指纹。
- 不接管 Edge 默认 profile，不连接远程调试端口。
- 不自动导出全部 Cookie、浏览历史、LocalStorage 或账号信息。
- 不处理 DRM/EME、Widevine 或付费授权绕过。
- 不在本期发布 Microsoft Edge Add-ons 商店。
- 不在后台扫描未被用户选中的其他标签页。

## 4. Edge 能力与约束

Microsoft Edge 基于 Chromium。Microsoft 官方文档确认 Chrome 扩展 API 和
manifest key 与 Edge 基本代码兼容；Edge Manifest V3 继续保留 `webRequest`
的观察能力，并支持 Native Messaging。

`webRequest` 只能观察扩展具有 host permission 的请求。播放器常从未知 CDN
域名加载媒体，因此仅授权当前网页域名会漏掉候选。本方案把
`http://*/*` 与 `https://*/*` 放入 `optional_host_permissions`：安装时不永久
获取，用户首次点击“开始捕获”时由 Edge 显示权限请求。即使用户已授权，扩展
仍只处理显式选中的 `tabId`。

## 5. 方案比较

### 5.1 方案一：Edge 扩展 + Native Messaging（采用）

扩展通过 `runtime.connectNative()` 与本机注册的 FireflyTools host 通信。
浏览器负责校验扩展 ID，host manifest 的 `allowed_origins` 只允许本扩展。

优点：浏览器提供调用方身份校验；不开放本地网络端口；适合后续打包和商店版。

代价：Windows 需要安装当前用户级 Native Messaging host；源码开发模式需要
生成稳定的 host 启动器。

### 5.2 方案二：扩展直接连接 localhost HTTP/WebSocket（不作为主通道）

优点：开发简单，不需要 Native Messaging 注册。

缺点：需要开放回环端口、实现配对 token、Origin 校验、端口发现和进程生命周期；
攻击面比 Native Messaging 更大。只保留为排障实验，不进入默认产品流程。

### 5.3 方案三：扩展复制候选 JSON（人工兜底）

扩展把一个经过脱敏和大小限制的候选 JSON 复制到剪贴板，用户在 FireflyTools
点击“粘贴 Edge 候选”。

优点：没有安装连接组件时仍能工作，便于定位扩展捕获与本地通信哪一层失败。

缺点：多一步人工操作，剪贴板可能被其他本机程序读取。复制内容默认不包含
Cookie、Authorization 或其他敏感头。

## 6. 总体架构

```text
用户操作的普通 Edge 标签页
        │ 已发生的媒体请求
        ▼
Edge Manifest V3 扩展
  ├─ 当前 tab 捕获状态
  ├─ webRequest 只读观察器
  ├─ 候选识别/去重
  └─ 候选确认 popup
        │ Native Messaging JSON
        ▼
FireflyTools Edge Native Host
  ├─ 校验扩展 origin 与消息 schema
  └─ 转发到当前 FireflyTools 进程
        │ 本机进程间消息
        ▼
EdgeCaptureReceiver
        │ Qt signal
        ▼
VideoDownloaderTool
  ├─ 显示候选与来源域名
  ├─ 用户确认导入
  └─ 复用现有 UniversalVideoSpider
```

## 7. 组件职责

### 7.1 Edge 扩展

建议目录：`browser_extensions/edge_video_capture/`

- `manifest.json`
  - `manifest_version: 3`
  - 权限：`activeTab`、`webRequest`、`nativeMessaging`、`storage`
  - 可选 host 权限：`http://*/*`、`https://*/*`
  - 固定开发版扩展 ID，保证 Native Messaging `allowed_origins` 稳定。
- `service_worker.js`
  - 维护 `tabId -> CaptureSession`。
  - 只监听正在捕获的 tab。
  - 监听请求 URL、请求头和响应 `Content-Type`，不修改请求。
  - 五分钟超时、标签页关闭或用户停止时立即清理。
- `candidate_detector.js`
  - URL 扩展名、query、MIME type 和 resource type 综合识别。
  - 支持 `.m3u8`、`.mp4`、`.mpd`，忽略 `blob:`、`data:`、广告关键词和重复 URL。
- `popup.html/js/css`
  - “开始/停止捕获”、当前标签页状态、权限提示、候选列表。
  - 候选显示类型、媒体域名、脱敏 URL 和发现时间。
  - “发送到 FireflyTools”和“复制候选 JSON”。
- `native_client.js`
  - 建立 Native Messaging port，发送候选并处理 ack/error。

扩展不得注入页面脚本，不得改变 DOM、请求头、响应或浏览器指纹。

### 7.2 Native Messaging Host

建议 Python 模块：`tools/edge_companion/native_host.py`。

- 按 Native Messaging 协议从 stdin 读取带长度前缀的 UTF-8 JSON。
- 校验消息大小、协议版本、消息类型和字段。
- 校验 Edge 传入的扩展 origin 与允许列表一致。
- 将候选转发到已运行 FireflyTools 的本机进程。
- FireflyTools 未运行时返回结构化 `APP_NOT_RUNNING`，不得自动下载。
- stdout 仅输出协议消息；诊断日志写 stderr 且必须脱敏。

Windows 开发安装使用当前用户级注册位置：
`HKCU\Software\Microsoft\Edge\NativeMessagingHosts\com.fireflytools.video_capture`。
安装和卸载必须由用户明确触发，不需要管理员权限。正式打包时提供独立
`fireflytools-edge-host.exe`；源码模式通过项目安装产生稳定 console-script
launcher，禁止把命令行参数拼接进注册表。

### 7.3 FireflyTools 接收器

建议模块：`tools/edge_companion/receiver.py`。

- 主程序启动时仅在 `127.0.0.1` 上绑定一个随机可用端口，禁止绑定
  `0.0.0.0`、局域网地址或 IPv6 公网地址。
- 每次应用启动生成 256-bit 随机 session token，并把 `port`、`token`、
  `pid`、`protocol_version` 和过期时间原子写入
  `%LOCALAPPDATA%\FireflyTools\runtime\edge_capture.json`。该文件沿用当前
  Windows 用户目录 ACL，应用退出时删除。
- Native host 读取运行描述文件、确认 PID 存活且描述未过期，再向回环接收器
  发送一次带 Bearer token 的 POST。扩展不能直接读取描述文件或访问接收器。
- 接收器只接受来自回环地址、携带当前 token、内容类型为 JSON 且不超过
  256 KiB 的 POST；连续认证失败触发短暂退避，但不在日志中输出 token。
- 消息进入 UI 前再次执行 schema、URL 和 header 校验。
- 通过 Qt signal 把候选送入 `VideoDownloaderTool`，后台线程不得直接操作控件。
- 接收器不自动启动下载，不持久化候选。

### 7.4 视频下载界面

在现有嗅探选项附近新增“Edge 辅助捕获”区域：

- 状态：`未安装 / 未连接 / 等待捕获 / 已收到候选 / 错误`。
- 按钮：`等待 Edge 捕获`、`粘贴 Edge 候选`。
- 收到候选后显示确认对话框，不直接入队。
- 对话框显示来源页面、媒体域名、类型、是否包含临时请求头和捕获时间。
- 用户确认后填入当前任务 URL，并把 Edge 请求头快照冻结到任务字典。
- 候选超过配置时效后入队或执行时，提示重新捕获。

现有 Playwright 模式继续保留，默认行为不变；Edge 模式是独立入口，不能和
Playwright 嗅探在同一任务中同时运行。

## 8. 消息协议

扩展发送的 V1 候选示例：

```json
{
  "protocol_version": 1,
  "type": "media_candidate",
  "request_id": "uuid",
  "captured_at": "2026-08-30T12:00:00Z",
  "page": {
    "url": "https://example.test/watch/1",
    "title": "Example"
  },
  "candidate": {
    "url": "https://cdn.example.test/master.m3u8?token=<opaque>",
    "kind": "hls",
    "content_type": "application/vnd.apple.mpegurl",
    "method": "GET",
    "headers": {
      "Referer": "https://example.test/",
      "Origin": "https://example.test",
      "User-Agent": "<edge user agent>"
    }
  },
  "sensitive_headers_included": false
}
```

约束：

- 单条消息最大 256 KiB，URL 最大 16 KiB，标题最大 512 字符。
- 只接受 `http` 和 `https` 候选；拒绝 `file`、`data`、`blob`、`javascript`。
- Header 名和值禁止 CR/LF，默认只允许 `Referer`、`Origin`、`User-Agent`、
  `Accept`、`Accept-Language`、`Range`。
- query 中 token 不解析、不展示原值，日志继续使用现有脱敏函数。
- Cookie 与 Authorization 在 V1 永远不进入协议。

## 9. 捕获与下载流程

1. 用户启动 FireflyTools，并点击“等待 Edge 捕获”。
2. 用户在 Edge 中正常打开目标页并完成安全验证。
3. 用户打开扩展 popup，点击“开始捕获当前标签页”。
4. 若尚未授权，Edge 弹出可选 host permission 请求。
5. 用户返回网页并点击播放。
6. 扩展观察媒体请求、识别候选并更新 popup。
7. 用户选择候选并点击“发送到 FireflyTools”。
8. Native host 与 FireflyTools 各自校验消息。
9. FireflyTools 弹出候选确认对话框。
10. 用户确认后，URL 和安全请求头写入当前任务快照。
11. 用户按现有流程设置名称、目录并入队下载。

## 10. 异常流程

- **未授权 host permission：** 扩展不开始监听，解释未知 CDN 需要运行时权限。
- **FireflyTools 未运行：** popup 显示“请先打开 FireflyTools”，保留候选供复制。
- **Native host 未安装：** 显示安装说明和“复制候选 JSON”。
- **没有候选：** 提示用户在开始捕获后重新点击播放；不自动刷新网页。
- **候选过期：** 下载器返回专用错误并建议重新捕获，不无条件重试。
- **下载返回 401/403：** 明确说明当前媒体需要浏览器会话；V1 不自动抓取敏感头。
- **候选过多：** 每个 tab 最多保留 50 条，优先 HLS/DASH、后保留较新的 MP4。
- **扩展或 host 协议版本不一致：** 拒绝消息并提示升级对应组件。

## 11. 安全与隐私

- 权限在用户点击开始捕获时申请，停止捕获后立即停止处理事件。
- 只以 `tabId` 为范围处理网络事件，即使扩展已获得较宽 host permission。
- 不使用 `cookies` 权限，不读取 Cookie 数据库。
- 不保存完整 HAR，不记录完整带 token URL。
- Native host `allowed_origins` 只包含开发版或商店版的确定扩展 ID。
- 安装器只修改当前用户 Edge NativeMessagingHosts 注册项，卸载可恢复。
- 所有候选在应用退出、扩展停止或超时后清空。
- 用户必须对下载内容拥有访问和保存权限；不支持 DRM 规避。

## 12. 与现有项目的集成点

预计新增：

- `browser_extensions/edge_video_capture/`
- `tools/edge_companion/native_host.py`
- `tools/edge_companion/protocol.py`
- `tools/edge_companion/receiver.py`
- `tools/edge_companion/install.py`
- `tests/test_edge_companion_protocol.py`
- `tests/test_edge_companion_receiver.py`

预计修改：

- `tools/main.py`：启动和关闭本地接收器。
- `tools/video_downloader.py`：连接状态、候选确认、任务快照和粘贴兜底。
- `tools/video_crawler/models.py`：增加可序列化的 Edge 候选/请求上下文模型。
- `tools/video_crawler/spider.py`：接受 Edge 捕获的安全请求头快照。
- `docs/项目介绍.md`：安装、权限、使用流程与限制。

不得把扩展逻辑塞入现有 `sniffer.py`；Edge 捕获与 Playwright 嗅探必须是两个
边界清晰的输入适配器。

## 13. 测试策略

### 13.1 Python 单元测试

- 协议字段、版本、大小、URL scheme 和 header 注入校验。
- token URL 与敏感 header 的日志脱敏。
- Native host 长度前缀消息读写和错误响应。
- 接收器线程到 Qt signal 的传递。
- UI 默认未连接、收到候选、确认、拒绝、过期和旧任务兼容。
- Edge 请求头快照能传给现有 HLS/MP4/DASH 适配器。

### 13.2 扩展测试

- URL/MIME 候选识别与广告过滤。
- 只处理激活 capture session 的 tabId。
- 重复候选、50 条上限、五分钟超时与 tab 关闭清理。
- 权限拒绝、Native host 断开、ack/error 和剪贴板兜底。
- 确认扩展不调用 blocking webRequest、不注入脚本、不修改请求。

### 13.3 人工验收

- 在 Edge Stable 通过目标站点验证并进入播放器。
- 开始捕获后播放，扩展显示至少一个真实 HLS/MP4/DASH 候选。
- FireflyTools 收到候选且日志不出现 Cookie、Authorization 或完整 token。
- 关闭捕获后其他标签页请求不会进入扩展候选列表。
- 卸载连接组件后，剪贴板兜底仍能把非敏感候选导入应用。

## 14. 分阶段交付

### 阶段一：可验证的最小闭环

- Edge 扩展捕获与候选 popup。
- 复制候选 JSON。
- FireflyTools 粘贴、校验、确认和下载。

该阶段先证明目标站点的真实媒体请求能够被普通 Edge 捕获，不依赖 Native
Messaging 安装与打包。

### 阶段二：Native Messaging 自动传递

- native host、当前用户安装/卸载、IPC 接收器和连接状态。
- 自动发送、ack/error、版本协商。

### 阶段三：受控增强

- 根据真实 401/403 证据决定是否设计按候选、一次性、内存态的敏感会话信息。
- 该阶段必须重新评审安全规格，不能在阶段一或二中预先加入 Cookie 抓取。

## 15. 已确认决策

- 浏览器目标：Microsoft Edge Stable。
- 主方案：普通 Edge 扩展辅助捕获，不再依赖自动化浏览器进入目标页面。
- 主通信：Native Messaging。
- 初期分发：开发者模式本地加载扩展，不发布商店。
- 默认安全边界：不捕获 Cookie/Authorization，不支持 DRM，不自动开始下载。
- 实施顺序：先完成剪贴板最小闭环，再接入 Native Messaging。

## 16. 参考资料

- Microsoft Edge 扩展概览：
  https://learn.microsoft.com/en-us/microsoft-edge/extensions/
- 从 Chrome 扩展移植到 Edge：
  https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/port-chrome-extension
- Edge Manifest V3 与 webRequest：
  https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/migrate-your-extension-from-manifest-v2-to-v3
- Edge Native Messaging：
  https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/native-messaging
- Edge 扩展 API 支持：
  https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/api-support
