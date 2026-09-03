# FireflyTools Edge 辅助视频捕获

这个扩展用于一种受限场景：视频能在用户自己的普通 Microsoft Edge 中播放，但站点限制 Playwright 自动化浏览器。它只在用户明确授权后接受当前选中标签页的媒体候选，并把经过约束的候选 JSON 通过剪贴板交给本机 FireflyTools；这不是 stealth、自动化伪装或人机验证绕过方案。

## 安全、隐私与功能边界

- 用户点击 Start/“开始捕获”后，Edge 权限弹窗请求 `http://*/*` 与 `https://*/*`，即全部 HTTP(S) 源的运行时可选 host 权限。权限范围是全 HTTP(S) 源；捕获控制器的逻辑过滤只接受用户选中并开始捕获的单个标签页。用户可随时点击 Stop，也可在扩展管理中撤销 host 权限。
- webRequest 回调可能接收到请求 Header；代码只保留 `Referer`、`Origin`、`User-Agent`、`Accept`、`Accept-Language`、`Range` 六个安全白名单 Header。`Cookie`、`Authorization` 不保留、不复制，也不转交给 FireflyTools 或剪贴板。扩展不使用 blocking webRequest、不修改页面，也不代替用户或绕过人机验证。
- 扩展不处理 DRM。检测到受保护内容时，应停止并按不支持处理。
- 依赖敏感浏览器会话的站点即使能在 Edge 中播放，下载请求仍可能返回 401/403。遇到这种情况，只保留不含敏感值的诊断并失败；不得扩展传输协议，也不得导出 Cookie、Authorization 或其他敏感 Header。
- 每次候选的有效期为 5 分钟；扩展只捕获用户选择并开始捕获的单个标签页，其他标签页不会加入候选。
- 点击 Stop/“停止捕获”会立即清空所选标签页的候选与临时捕获状态。
- 普通 URL/Playwright 输入与 Edge 候选输入互斥。确认 Edge 候选后，不再走 Playwright；清除或成功入队后才恢复普通 URL 模式。

## 安装与完整使用流程

1. 打开 `edge://extensions`，启用“开发人员模式”。
2. 选择“加载解压缩的扩展”，目录选择 `browser_extensions/edge_video_capture`。
3. 核对扩展 ID 必须是 `applbmkghgaoadhmmcdnbmebgideiefg`；不一致时停止使用并检查加载目录与 `manifest.json`。
4. 启动 FireflyTools，打开“视频下载”功能。
5. 在普通 Microsoft Edge 中通过站点允许的人工安全验证，进入真实播放器。
6. 点击扩展，为当前标签页“开始捕获”；在这次用户手势中，Edge 会请求 `http://*/*` 与 `https://*/*` 全部 HTTP(S) 源的运行时可选 host 权限。批准后，捕获控制器仍只接受当前选中的单个标签页。
7. 回到页面点击播放。
8. 在扩展候选中选择一个 HLS、MP4 或 DASH，点击“复制候选 JSON”。
9. 回到 FireflyTools，点击“粘贴 Edge 候选”，检查不含敏感值的确认框并确认。
10. 选择名称和输出目录，加入现有下载队列。

候选过期、格式无效或安全校验失败时，FireflyTools 会保留当前安全输入状态并拒绝入队。确认框只展示脱敏后的必要元数据，不应显示原始 token 查询值。原始候选 JSON 的 URL 仍可能带有短期 token，不要把原始剪贴板 JSON 提交到 Issue、日志、聊天或仓库。

候选确认后，同一个按钮会从“粘贴 Edge 候选”变为“清除 Edge 候选”。点击它会直接清除候选，不读取剪贴板、不再弹确认框，并恢复普通 URL/Playwright 模式；若目标网址仍是候选媒体 URL，会同时清空，若用户已经手工改成其他 URL，则保留手工输入。

## 安装依赖与开发测试

在 Edge 中“加载解压缩的扩展”不需要执行 `npm install`，仓库也没有为此扩展声明第三方 npm 依赖。Node 仅作为开发测试运行时，不参与扩展安装或日常捕获。

开发者可从扩展目录使用 Node 内置测试运行器执行四个测试文件；请使用开发环境中 Node 可执行文件的绝对路径：

```powershell
& '<Node 可执行文件的绝对路径>' --test tests/candidate_detector.test.js tests/capture_store.test.js tests/capture_controller.test.js tests/popup_model.test.js
```

## Stage 1 人工验收记录

以下项目必须在真实 Microsoft Edge 和真实目标站点上由用户人工执行。它们保留到最终发布门禁，不阻塞 Stage 2 编码；自动化测试结果不能替代这些证据，也不得据此声称目标站已 PASS。自动化证据见下一节。

| 验收项 | 状态 | 证据 |
| --- | --- | --- |
| fixed extension ID matches | 待人工执行 | —（需真实 Edge） |
| permission requested only after Start click | 待人工执行 | —（需真实 Edge） |
| Play 后 10 秒内出现真实 candidate | 待人工执行 | —（需真实 Edge 与目标站） |
| other tabs do not add candidates | 待人工执行 | —（需真实 Edge） |
| Stop immediately clears selected tab candidates | 待人工执行 | —（需真实 Edge） |
| clipboard JSON contains no Cookie/Authorization field/value | 待人工执行 | —（需真实 Edge） |
| FireflyTools confirmation does not display raw token query | 待人工执行 | —（需真实 Edge 与目标站） |
| confirmed HLS/MP4/DASH enters normal queue without Playwright | 待人工执行 | —（需真实 Edge 与目标站） |

## 自动化验证

验证日期：2026-09-02。

- 固定 ID：从 `manifest.json` 的公钥独立推导出 `applbmkghgaoadhmmcdnbmebgideiefg`，与预期一致；Python manifest contract 测试也通过。
- 扩展测试：使用 Codex 内置 Node 的绝对路径运行四个 JS 测试文件，`tests 41`、`pass 41`、`fail 0`、`skipped 0`。
- Python 完整套件：在 worktree 内设置 `TEMP`/`TMP`，并设置 `QT_QPA_PLATFORM=offscreen`、`PYTHONDONTWRITEBYTECODE=1` 后运行 `python -m unittest discover -s tests -v`。可信的沙箱外复跑结果为 `Ran 291 tests in 31.772s`，`FAILED (failures=1, skipped=1)`，因此完整套件不能记为通过。
  - 唯一失败是与 Edge 捕获无关的图片相似度性能阈值抖动：`test_same_phash_collision_uses_dhash_index_for_ten_thousand_items` 用时 `10.810s`，超过 `10.0s` 门槛。
  - 随后只聚焦复跑该性能项，结果为 `Ran 1 test in 3.625s`、`OK`；未修改图片模块，完整套件原始失败仍保留在本记录中。
  - 唯一 skip 是 Windows 当前进程缺少创建符号链接权限（`WinError 1314`），不代表 Edge 捕获业务断言失败。
- `git diff --check`：通过；仅出现已跟踪 `docs/项目介绍.md` 后续可能由 LF 转为 CRLF 的行尾提示，没有空白错误。

首次在受限沙箱内运行 Node/Python 时分别遇到子进程 `spawn EPERM` 和临时目录 `WinError 5`；这些受基础设施权限影响的运行未被记作通过，以上计数来自权限条件明确的复跑。
