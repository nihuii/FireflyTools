# FireflyTools Edge 辅助视频捕获

这个扩展用于一种受限场景：视频能在用户自己的普通 Microsoft Edge 中播放，但站点限制 Playwright 自动化浏览器。它只在用户明确授权后接受当前选中标签页的媒体候选，默认通过 Native Messaging 把用户选中的候选发送给本机 FireflyTools；本机连接不可用时仍可复制候选 JSON 并手工粘贴。这不是 stealth、自动化伪装或人机验证绕过方案。

## 安全、隐私与功能边界

- 加载或安装扩展时，Edge 会请求 `http://*/*` 与 `https://*/*`，即全部 HTTP(S) 网页和 CDN 的必要 host 权限，以便后台 `webRequest` 监听器可靠注册。权限范围是全 HTTP(S) 源；捕获控制器只保留用户点击“开始捕获”后唯一选中标签页的请求。用户可随时点击 Stop，也可在扩展管理中撤销 host 权限；撤销后捕获不可用。
- 在已获准的 HTTP(S) 范围内，webRequest 回调技术上可能接收到请求 Header；控制器只保留所选标签页的数据，代码也只保留 `Referer`、`Origin`、`User-Agent`、`Accept`、`Accept-Language`、`Range` 六个安全白名单 Header。`Cookie`、`Authorization` 会立即丢弃，不保留、不复制，也不转交给 FireflyTools 或剪贴板。扩展不使用 blocking webRequest、不修改页面，也不代替用户或绕过人机验证。
- 扩展不处理 DRM。检测到受保护内容时，应停止并按不支持处理。
- 依赖敏感浏览器会话的站点即使能在 Edge 中播放，下载请求仍可能返回 401/403。遇到这种情况，只保留不含敏感值的诊断并失败；不得扩展传输协议，也不得导出 Cookie、Authorization 或其他敏感 Header。
- 每次候选的有效期为 5 分钟；扩展只捕获用户选择并开始捕获的单个标签页，其他标签页不会加入候选。
- 点击 Stop/“停止捕获”会立即清空所选标签页的候选与临时捕获状态。
- 普通 URL/Playwright 输入与 Edge 候选输入互斥。确认 Edge 候选后，不再走 Playwright；清除或成功入队后才恢复普通 URL 模式。

### 数据安全清单

| Boundary | Stored/transmitted | Explicitly excluded |
|---|---|---|
| Extension session | selected tab ID, page URL/title, up to 50 media candidates, safe headers | Cookie, Authorization, history, LocalStorage, HAR |
| Clipboard/native message | one user-selected V1 candidate | other tabs and unselected candidates |
| Runtime descriptor | loopback port, random token, PID, version, expiry | candidate URL and browser data |
| Download task | confirmed media URL and safe header snapshot | browser profile, Cookie database, DRM keys |

Native host 只从当前用户的短期运行时描述文件读取端口和随机 token，并把已校验的候选发送到 `http://127.0.0.1:<随机端口>/v1/candidate`。接收器只监听回环地址；本机请求使用 `Authorization: Bearer <随机 token>` 做进程间认证，这不是网页请求的 Authorization，也不会进入候选或下载任务。Native host 禁用环境代理且拒绝重定向，避免把 token 或候选转发到其他地址。

## Stage 2 开发安装、状态与卸载

在仓库根目录激活用于运行 FireflyTools 的 Python 环境后，可使用以下精确命令：

```powershell
python -m pip install -e . --no-deps
python -m tools.edge_companion.install install
python -m tools.edge_companion.install status
python -m tools.edge_companion.install uninstall
```

前三条依次生成当前 Python 环境的 `fireflytools-edge-host.exe`、安装当前用户连接组件并只读检查状态。第四条只在不再使用自动发送时执行；不要在刚安装后紧接着执行。安装器不需要管理员权限，只写入当前用户的 `HKCU\Software\Microsoft\Edge\NativeMessagingHosts\com.fireflytools.video_capture` 和 `%LOCALAPPDATA%\FireflyTools\edge_companion\com.fireflytools.video_capture.json`。`status` 不安装或修复任何内容。

`uninstall` 只删除上述当前用户注册项和由安装器生成的 manifest；不会删除父目录、解压缩扩展、仓库文件、Python 环境或下载内容，也不会卸载 editable Python 包。

## 完整使用流程

1. 按上一节执行 editable 安装、连接组件 `install` 和 `status`；确认状态输出“Edge 连接组件已安装”。
2. 打开 `edge://extensions`，启用“开发人员模式”。
3. 选择“加载解压缩的扩展”，目录选择 `browser_extensions/edge_video_capture`。
4. 核对扩展 ID 必须是 `applbmkghgaoadhmmcdnbmebgideiefg`；不一致时停止使用并检查加载目录与 `manifest.json`。
5. 启动 FireflyTools，打开“视频下载”功能，点击“等待 Edge 捕获”；状态应变为“等待捕获”。
6. 在普通 Microsoft Edge 中通过站点允许的人工安全验证，进入真实播放器。
7. 加载扩展时接受 Edge 显示的 HTTP(S) 网页和 CDN 访问权限；随后点击扩展，为当前标签页“开始捕获”。开始按钮不会再次请求权限，捕获控制器只保留当前选中的单个标签页。
8. 回到页面点击播放；在扩展候选中选择一个 HLS、MP4 或 DASH，点击“发送到 FireflyTools”。
9. FireflyTools 收到候选后会结束本次等待并弹出不含敏感值的确认框；人工确认后候选才成为当前输入。
10. 选择名称和输出目录，加入现有下载队列。

若 Native Messaging 不可用，在第 8 步改点“复制候选 JSON”，再回到 FireflyTools 点击“粘贴 Edge 候选”。剪贴板路径执行同一套 V1 校验和确认，不会绕过候选过期或安全 Header 限制。

候选过期、格式无效或安全校验失败时，FireflyTools 会保留当前安全输入状态并拒绝入队。确认框只展示脱敏后的必要元数据，不应显示原始 token 查询值。原始候选 JSON 的 URL 仍可能带有短期 token，不要把原始剪贴板 JSON 提交到 Issue、日志、聊天或仓库。

候选确认后，同一个按钮会从“粘贴 Edge 候选”变为“清除 Edge 候选”。点击它会直接清除候选，不读取剪贴板、不再弹确认框，并恢复普通 URL/Playwright 模式；若目标网址仍是候选媒体 URL，会同时清空，若用户已经手工改成其他 URL，则保留手工输入。

## FireflyTools 的五种 Edge 状态

| 状态 | 含义与下一步 |
|---|---|
| 未安装 | 当前用户 Native Messaging 注册或生成的 manifest/launcher 不可用；运行 `status` 排查，按需执行 `install`。剪贴板粘贴回退仍可用。 |
| 未连接 | 连接组件可用，FireflyTools 本机接收器已启动，但尚未允许接收下一条候选；点击“等待 Edge 捕获”。 |
| 等待捕获 | 本机接收器的一次性接收门已开启；此时在 Edge 中选择候选并发送。再次点击“停止等待”只关闭接收门，不操作网页。 |
| 已收到候选 | 候选已送到确认流程；确认后成为互斥的 Edge 输入，清除或成功入队后恢复普通 URL/Playwright 模式。 |
| 错误 | 本机接收器报告运行错误；查看状态提示，保留候选时可改用复制/粘贴回退。 |

## 常见故障与恢复

- **`HOST_NOT_INSTALLED` / “未安装”：** 在同一个 Python 环境运行 `python -m tools.edge_companion.install status`。若未安装，先确认 `fireflytools-edge-host.exe` 可由当前环境找到，再执行 `install`；仍可使用复制/粘贴回退。
- **`APP_NOT_RUNNING`：** 先启动 FireflyTools。应用启动时才会在 `127.0.0.1` 创建短期接收器和运行时描述文件；连接组件不会代替用户启动应用或下载。
- **`APP_NOT_WAITING`：** FireflyTools 已运行但没有处于“等待捕获”；在视频下载页点击“等待 Edge 捕获”后重新发送。
- **扩展 ID 不匹配：** Native host manifest 的 `allowed_origins` 和 host 自身都只接受 `chrome-extension://applbmkghgaoadhmmcdnbmebgideiefg/`。若 `edge://extensions` 显示其他 ID，停止使用，确认加载的是本仓库目录且 `manifest.json` 的固定 `key` 未被改动；错误 ID 不能连接本机 host。
- **企业 Native Messaging 策略限制：** 受组织管理的 Edge 可能通过 `NativeMessagingAllowlist` 等策略只允许指定 host。若策略未允许 `com.fireflytools.video_capture`，应用和扩展不能自行绕过；联系管理员放行，或使用复制/粘贴回退。
- **没有候选：** 扩展只观察“开始捕获”之后当前标签页新发生的请求，不重放之前已经完成的网络请求，也不自动刷新网页或点击播放器。保持捕获开启，回到同一标签页重新点击播放，再查看候选列表。
- **候选过期：** 捕获会话和候选有效期为 5 分钟；扩展清理后或 FireflyTools 在确认、入队、执行时判定过期，都应重新捕获，不要无条件重试旧候选。
- **下载返回 401/403：** V1 只携带 `Referer`、`Origin`、`User-Agent`、`Accept`、`Accept-Language`、`Range` 安全 Header，不携带网页 Cookie 或 Authorization。依赖敏感浏览器会话的媒体因此可能只能在 Edge 中播放而无法下载；只记录脱敏诊断并停止，不导出敏感会话头、不扩展协议重试。
- **`TIMEOUT` 或协议版本不一致：** 保留候选并改用复制/粘贴；同时确认扩展、editable 包和 FireflyTools 来自同一版本。

## 安装依赖与开发测试

在 Edge 中“加载解压缩的扩展”不需要执行 `npm install`，仓库也没有为此扩展声明第三方 npm 依赖。Node 仅作为开发测试运行时，不参与扩展安装或日常捕获。

开发者可使用 Node 内置测试运行器执行六个测试文件；请使用开发环境中 Node 可执行文件的绝对路径：

```powershell
& '<Node 可执行文件的绝对路径>' --test browser_extensions/edge_video_capture/tests/candidate_detector.test.js browser_extensions/edge_video_capture/tests/capture_store.test.js browser_extensions/edge_video_capture/tests/capture_controller.test.js browser_extensions/edge_video_capture/tests/popup_model.test.js browser_extensions/edge_video_capture/tests/popup_interaction.test.js browser_extensions/edge_video_capture/tests/native_client.test.js
```

## Stage 1 人工验收记录

以下项目必须在真实 Microsoft Edge 和真实目标站点上由用户人工执行。它们保留到最终发布门禁，不阻塞 Stage 2 编码；自动化测试结果不能替代这些证据，也不得据此声称目标站已 PASS。自动化证据见下一节。

| 验收项 | 状态 | 证据 |
| --- | --- | --- |
| fixed extension ID matches | 待人工执行 | —（需真实 Edge） |
| HTTP(S) host permission requested during extension load, with no second prompt after Start | 待人工执行 | —（需真实 Edge） |
| Play 后 10 秒内出现真实 candidate | 待人工执行 | —（需真实 Edge 与目标站） |
| other tabs do not add candidates | 待人工执行 | —（需真实 Edge） |
| Stop immediately clears selected tab candidates | 待人工执行 | —（需真实 Edge） |
| clipboard JSON contains no Cookie/Authorization field/value | 待人工执行 | —（需真实 Edge） |
| FireflyTools confirmation does not display raw token query | 待人工执行 | —（需真实 Edge 与目标站） |
| confirmed HLS/MP4/DASH enters normal queue without Playwright | 待人工执行 | —（需真实 Edge 与目标站） |

## 自动化验证

验证日期：2026-09-02。

- 固定 ID：从 `manifest.json` 的公钥独立推导出 `applbmkghgaoadhmmcdnbmebgideiefg`，与预期一致；Python manifest contract 测试也通过。
- 扩展测试：2026-09-05 使用 Codex 内置 Node 的绝对路径运行六个 JS 测试文件，`tests 50`、`pass 50`、`fail 0`、`skipped 0`。
- Python 完整套件：在 worktree 内设置 `TEMP`/`TMP`，并设置 `QT_QPA_PLATFORM=offscreen`、`PYTHONDONTWRITEBYTECODE=1` 后运行 `python -m unittest discover -s tests -v`。可信的沙箱外复跑结果为 `Ran 291 tests in 31.772s`，`FAILED (failures=1, skipped=1)`，因此完整套件不能记为通过。
  - 唯一失败是与 Edge 捕获无关的图片相似度性能阈值抖动：`test_same_phash_collision_uses_dhash_index_for_ten_thousand_items` 用时 `10.810s`，超过 `10.0s` 门槛。
  - 随后只聚焦复跑该性能项，结果为 `Ran 1 test in 3.625s`、`OK`；未修改图片模块，完整套件原始失败仍保留在本记录中。
  - 唯一 skip 是 Windows 当前进程缺少创建符号链接权限（`WinError 1314`），不代表 Edge 捕获业务断言失败。
- `git diff --check`：通过；仅出现已跟踪 `docs/项目介绍.md` 后续可能由 LF 转为 CRLF 的行尾提示，没有空白错误。

首次在受限沙箱内运行 Node/Python 时分别遇到子进程 `spawn EPERM` 和临时目录 `WinError 5`；这些受基础设施权限影响的运行未被记作通过，以上计数来自权限条件明确的复跑。
