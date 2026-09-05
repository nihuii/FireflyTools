# Edge 首请求捕获设计

## 背景与目标

FireflyTools Edge 视频捕获扩展只记录用户点击“开始捕获”之后当前标签页新发生的网络请求。部分站点会在页面初始化时立即请求主视频清单；用户打开扩展时该请求已经完成，因此扩展可能漏掉主视频，只看到随后加载的广告或第三方媒体流。

本次目标是在保留现有“开始捕获”行为的同时，新增一个明确的“开始捕获并重新加载”入口。它必须先建立并持久化捕获会话，再绕过缓存重新加载当前标签页，使页面初始化阶段的 HLS、DASH 或 MP4 请求进入现有捕获链路。

## 已批准方案

扩展弹窗保留现有“开始捕获”按钮，并新增“开始捕获并重新加载”按钮。用户选择新按钮后执行以下顺序：

1. 查询当前活动标签页并发送现有 `capture:start` 消息。
2. 等待后台返回成功；该响应代表捕获控制器状态已经持久化。
3. 调用 `chrome.tabs.reload(tabId, {bypassCache: true})` 重新加载同一标签页。
4. 弹窗显示“已开始捕获并重新加载页面”状态；后台继续使用现有顶层 `webRequest` 监听器收集该标签页的新请求。

若建立捕获会话失败，不得重新加载页面。若会话已建立但重新加载失败，弹窗应显示独立失败提示；捕获会话保持有效，用户仍可手动刷新或点击播放。重复点击期间按钮应避免并发触发同一流程。

## 交互与兼容性

- 现有“开始捕获”不自动刷新，避免破坏表单、播放进度或已经完成的人工验证。
- 新按钮名称明确表达会重新加载页面，不需要额外确认框。
- 新流程只作用于用户当前选择的标签页，不导航或操作其他标签页。
- 强制刷新可能重新触发站点安全验证；捕获会话在同一标签页继续保持，用户可完成人工验证后再播放。
- 候选列表、Stop、复制 JSON、Native Messaging 和五分钟会话有效期保持不变。

## 安全与非目标

- 不增加 content script、`scripting`、Cookie、Authorization、请求修改、stealth 或验证码自动化能力。
- 不解析站点专用的 `player_aaaa` 或其他页面变量。
- 不自动选择、下载或判断候选是否为主视频；本次只保证能够观察重新加载后最早出现的请求。
- `chrome.tabs.reload()` 使用现有标签页能力，不扩大当前已声明的 HTTP(S) host 权限范围。

## 代码边界

预计修改：

- `browser_extensions/edge_video_capture/popup.html`：新增按钮。
- `browser_extensions/edge_video_capture/popup.js`：复用开始捕获流程，并在成功后调用 `chrome.tabs.reload()`。
- `browser_extensions/edge_video_capture/popup_model.js`：新增成功和刷新失败提示（若现有提示映射由该模块统一管理）。
- `browser_extensions/edge_video_capture/tests/popup_interaction.test.js`：覆盖严格时序、成功路径和失败路径。
- `browser_extensions/edge_video_capture/README.md` 与 `docs/项目介绍.md`：更新使用流程和故障排查。

不得修改 Python 下载器、Native Host、HLS 解析器或站点专用逻辑。

## 测试与验收

自动化测试至少验证：

1. 新按钮先发送 `capture:start`，收到成功响应后才调用 `tabs.reload`。
2. `tabs.reload` 使用当前标签页 ID 和 `{bypassCache: true}`。
3. `capture:start` 失败时不重新加载。
4. 重新加载失败时显示刷新失败，但不发送 Stop 或清除已建立的捕获会话。
5. 原“开始捕获”、Stop、候选选择、复制和发送行为不回归。

人工验收步骤：打开目标视频页，点击“开始捕获并重新加载”，必要时完成人工验证，等待播放器加载，然后确认候选列表出现页面初始主视频的 `cdn16.11yun.space/...m3u8` 请求。人工验收结果不得由自动化测试替代。
