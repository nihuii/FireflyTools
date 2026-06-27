# FireflyTools 项目介绍（新版）

> 用途：给新对话或新接手的大模型快速理解项目现状。  
> 更新日期：2026-06-27  
> 当前说明以代码、测试和 `docs/plans/` 中的实施记录为依据，不混入旧版过期描述。

## 1. 项目定位

FireflyTools（流萤媒体工具箱）是一个基于 Python + PyQt6 的桌面媒体工具箱，包含视频下载爬虫、视频文件整理、关键字归档和图片批处理能力。

主入口：

```powershell
python -m tools.main
```

主窗口类：

```text
tools/main.py -> MediaToolboxApp
```

当前 UI 使用无边框圆角窗口、壁纸背景、动态 QSS 主题、标签页布局和后台线程/异步任务，避免耗时下载或文件操作阻塞界面。

## 2. 技术栈

- Python 3.10+
- PyQt6：桌面 UI
- Pillow：图片处理与主题色提取
- requests / aiohttp：同步与异步网络请求
- m3u8：HLS playlist 解析
- cryptography：HLS AES 解密
- Playwright：网页媒体请求嗅探
- FFmpeg：切片合并、容器修复、音视频 mux
- yt-dlp：可选外部后备下载引擎
- unittest：当前测试框架

依赖安装参考：

```powershell
python -m pip install -r requirements.txt
playwright install chromium
```

Windows 下仓库内的 `tools/ffmpeg.exe` 会在 `tools` 包初始化时自动加入子进程 `PATH`；Linux 和 macOS 仍需自行安装 FFmpeg。

## 3. 顶层结构

```text
FireflyTools/
├─ tools/
│  ├─ main.py                         # 主窗口、标签页、壁纸切换、动态主题应用
│  ├─ theme_utils.py                  # 动态 QSS、阴影、明暗主题和 QMessageBox 样式
│  ├─ video_downloader.py             # 视频下载爬虫 UI、队列、批次结果
│  ├─ video_extractor.py              # 视频子目录提取工具
│  ├─ keyword_organizer.py            # 关键字归档工具
│  ├─ image_resizer.py                # 图片智能裁剪工具
│  ├─ runtime_setup.py                # bundled FFmpeg 运行环境配置
│  ├─ ffmpeg.exe                      # FFmpeg 可执行文件
│  └─ video_crawler/                  # 视频爬虫核心包
├─ tests/                             # unittest 测试
├─ docs/
│  ├─ project-overview.md             # 当前这份交接说明
│  ├─ plans/                          # 视频爬虫路线图和实施计划
│  └─ superpowers/                    # 设计与实施记录
├─ pic/                               # 全部内置 UI 壁纸
├─ requirements.txt                   # Python 运行依赖
├─ LICENSE                            # MIT License
└─ README.md                          # 面向用户的安装与使用说明
```

## 4. 主界面功能

`tools/main.py` 启动后注册四个标签页：

1. `VideoDownloaderTool`：视频下载爬虫。
2. `VideoExtractorTool`：视频子目录提取。
3. `KeywordOrganizerTool`：关键字归档。
4. `SmartImageResizerTool`：图片智能裁剪。

### 4.1 视频下载爬虫

UI 入口：

```text
tools/video_downloader.py -> VideoDownloaderTool
```

核心入口：

```text
tools/video_crawler/spider.py -> UniversalVideoSpider
```

当前视频爬虫不是固定站点白名单解析器，而是通用 MP4 / HLS / DASH 嗅探下载器，并支持可选 yt-dlp 后备。

UI 已实现：

- 输入目标 URL、保存名称、保存目录。
- 高速/稳定模式切换。
- 切片并发数设置。
- 复用未完成切片开关。
- 公开平台失败时尝试 yt-dlp 开关。
- HLS 直播录制秒数设置。
- 诊断链接按钮。
- 下载任务队列。
- 批次完成弹窗。
- 结构化错误统计。
- 只对可恢复失败显示“重试可恢复失败任务”。
- 日志和弹窗中的敏感信息脱敏。

### 4.2 视频子目录提取

文件：

```text
tools/video_extractor.py
```

功能：

- 扫描指定目录及子目录。
- 将深层视频文件移动到根目录。
- 支持重名保护。
- 可选清理空目录。

### 4.3 关键字归档

文件：

```text
tools/keyword_organizer.py
```

功能：

- 输入关键字。
- 扫描目录中文件名。
- 将包含关键字的图片/视频移动到对应归档目录。
- 只在匹配成功时创建目录。

### 4.4 图片智能裁剪

文件：

```text
tools/image_resizer.py
```

功能：

- 基于 Pillow 批量处理图片。
- 支持自定义输出分辨率。
- 支持透明通道转白底 RGB。
- 支持高质量缩放。
- 支持居中、保头、保脚等裁剪策略。

## 5. 视频爬虫核心架构

```text
tools/video_crawler/
├─ models.py                    # MediaKind、MediaCandidate、BrowserSessionSnapshot、DiagnosticReport
├─ errors.py                    # VideoErrorCode、VideoDownloadError
├─ logging_utils.py             # redact_for_display，统一脱敏
├─ reporting.py                 # format_diagnostic_report
├─ session.py                   # Cookie、Header、LocalStorage 相关会话继承
├─ sniffer.py                   # PageSniffer，Playwright 页面嗅探
├─ spider.py                    # UniversalVideoSpider，核心编排入口
├─ resume.py                    # SegmentManifest，断点续传状态
└─ adapters/
   ├─ base.py                   # VideoAdapter 协议、VideoDownloadOrchestrator
   ├─ direct_mp4.py             # DirectMp4Adapter
   ├─ hls.py                    # HlsAdapter
   ├─ dash.py                   # DashAdapter
   └─ ytdlp.py                  # YtDlpAdapter
```

核心数据类型：

- `MediaKind`: `DIRECT_MP4`、`HLS`、`DASH`、`DRM`、`UNKNOWN`
- `MediaCandidate`: 媒体候选流
- `BrowserSessionSnapshot`: 浏览器会话快照，包含 UA、Referer、Origin、Cookie、Header、LocalStorage
- `DiagnosticReport`: 诊断报告
- `VideoErrorCode`: 结构化错误码
- `VideoDownloadError`: 带错误码、详情和 retryable 标记的异常

适配器优先级：

```text
DirectMp4Adapter: 100
HlsAdapter: 80
DashAdapter: 60
YtDlpAdapter: 外部后备，不在默认 orchestrator 中
```

yt-dlp 后备触发条件：

- UI 勾选“公开平台失败时尝试 yt-dlp”。
- 内置路径失败错误码是 `NO_MEDIA_FOUND` 或 `UNSUPPORTED_DASH`。
- 本机安装了 `yt-dlp`。

## 6. 视频爬虫已实现能力

### 6.1 直链和网页嗅探

- 支持直接 `.mp4` 下载。
- 支持直接 `.m3u8` 下载。
- 支持直接 `.mpd` 静态 DASH 下载。
- 对普通网页使用 Playwright headless 监听网络响应。
- 能从响应 URL 或 `content-type` 中识别 MP4 / M3U8 / MPD。
- 多个 M3U8 候选时，通过切片数量探测优先选择更像正片的流。

### 6.2 HLS

- Variant Playlist 自动选择最高带宽流。
- AES-CBC 解密。
- 显式 IV 和默认 IV。
- `EXT-X-BYTERANGE` Range 请求。
- fMP4 `EXT-X-MAP` 初始化片段。
- `DISCONTINUITY` 和多 init map 的分组策略。
- 默认音轨下载并 mux。
- 默认字幕下载并以 `mov_text` mux。
- 直播滚动 playlist 轻量录制。
- 切片级断点续传，使用 `.firefly-segments.json`。
- 切片失败比例不超过 3% 时继续合并。
- FFmpeg 合并/修复失败返回 `FFMPEG_FAILED`。

### 6.3 DASH / MPD

当前 DASH 第一版支持：

- 无 DRM 静态 MPD。
- `SegmentTemplate`。
- 最高带宽视频轨和音频轨选择。
- video/audio 分段下载。
- track 级 manifest 断点续传。
- FFmpeg mux 成 MP4。
- DRM 内容明确拒绝为 `UNSUPPORTED_DRM`。

当前 DASH 不支持：

- 动态/live MPD。
- `SegmentTimeline`。
- DRM 绕过。
- 缺失音频轨或视频轨的复杂 MPD。
- 更复杂的 BaseURL 继承场景。

### 6.4 结构化错误和报告

当前错误码包括：

```text
UNKNOWN
NETWORK_TIMEOUT
HTTP_FORBIDDEN
HTTP_NOT_FOUND
NO_MEDIA_FOUND
UNSUPPORTED_DASH
UNSUPPORTED_DRM
M3U8_PARSE_FAILED
SEGMENT_FAILURE_RATE_EXCEEDED
FFMPEG_FAILED
EMPTY_OUTPUT
```

队列结果弹窗会显示：

- 成功/失败数量。
- 每个失败任务的错误码。
- 是否建议直接重试。
- 错误统计。
- yt-dlp 成功时标注外部引擎。

### 6.5 脱敏

以下内容在日志、诊断报告和弹窗中会被脱敏：

- Cookie
- Authorization
- X-Token
- token
- access_token
- auth
- signature
- sig

## 7. 当前限制和已知问题

### 7.1 不做的事

- 不绕过 Widevine、FairPlay、PlayReady 或任何 DRM。
- 不破解验证码、人机校验、账号权限、付费墙或地区限制。
- 不做高并发恶意请求。
- 不为大型平台优先写硬编码解析器。

### 7.2 当前技术边界

- `PageSniffer` 支持无头和可视化 Chromium，但验证码、登录、付费墙等操作只能由用户在可视化窗口中合法完成。
- 持久化 Playwright profile 仅保存在本机 `browser_profiles/`，其中可能包含 Cookie 和站点存储，禁止上传 Git。
- 能从网络响应以及 JSON / HTML / JS 响应正文提取媒体候选，但会过滤转义占位符等明显伪地址。
- 主页面 403 / 访问受限会归类为 `HTTP_FORBIDDEN`，但程序不会绕过站点权限控制。
- 多个 HLS 候选会综合切片数量、码率和分辨率选择正片；源站缺少画质元数据时仍只能依赖可获得的指标。

相关实施记录：

```text
docs/plans/video-crawler-access-limited-sniffing-implementation-plan.md
```

## 8. 已知调试结论：aowu.tv

用户给过失败 URL：

```text
https://www.aowu.tv/w/BNCxTD01jh6N#s=5249&ep=16
```

历史调试确认该站点可能因网络环境或空浏览器会话返回 403。后续阶段已经实现：

- 403 / 访问受限结构化诊断。
- 可视化嗅探和人工播放等待。
- 可选持久化 Chromium profile。
- JSON / HTML / JS 响应正文媒体候选提取。
- 伪 M3U8 地址过滤，以及候选流切片数、码率、分辨率联合选择。

这些能力用于改善诊断和合法会话复用，不保证绕过目标站点的访问策略。

## 9. 测试地图

```text
tests/test_video_downloader.py                 # UI、队列、批次结果、spider 兼容行为
tests/test_video_crawler_models.py             # 模型和结构化错误
tests/test_video_crawler_diagnostics.py        # 静态诊断、PageSniffer 分类、报告格式化
tests/test_video_crawler_session.py            # Cookie/Header/LocalStorage 会话继承
tests/test_video_crawler_redaction.py          # 敏感信息脱敏
tests/test_video_crawler_resume.py             # SegmentManifest
tests/test_video_crawler_hls.py                # HLS IV、BYTERANGE、MAP、DISCONTINUITY helper
tests/test_video_crawler_hls_renditions.py     # HLS 音轨和字幕
tests/test_video_crawler_hls_live.py           # HLS 直播录制
tests/test_video_crawler_structured_errors.py  # HLS FFmpeg 结构化错误
tests/test_video_crawler_dash.py               # DASH 解析、DRM、mux
tests/test_video_crawler_dash_resume.py        # DASH 分段断点续传
tests/test_video_crawler_adapters.py           # 适配器编排、spider 拆分兼容
tests/test_video_crawler_ytdlp.py              # yt-dlp 外部后备
tests/test_theme_utils.py                      # 主题和 QMessageBox 可读性
tests/test_video_crawler_sniffer_access.py     # 访问受限、可视化和持久会话嗅探
tests/test_main_window.py                      # 主窗口尺寸与壁纸背景结构
tests/test_runtime_setup.py                    # bundled FFmpeg 自动发现
```

完整测试：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

测试数量会随功能增长，以当前完整测试命令的输出为准。

常用局部测试：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_video_downloader -v
python -m unittest tests.test_video_crawler_diagnostics -v
python -m unittest tests.test_video_crawler_hls tests.test_video_crawler_hls_renditions tests.test_video_crawler_hls_live -v
python -m unittest tests.test_video_crawler_dash tests.test_video_crawler_dash_resume -v
```

## 10. 计划文档状态

```text
docs/plans/video-crawler-roadmap.md
```

早期路线图，很多“尚未实现”描述已经过期。适合了解问题来源，不应直接当成当前实现状态。

```text
docs/plans/video-crawler-phased-implementation-plan.md
```

原始 Phase 0-9 实施计划。Phase 1-9 已按代码和测试回填为完成。Phase 0 是 Git/基线检查流程项。

```text
docs/plans/video-crawler-gap-closure-implementation-plan.md
```

缺口补齐计划，Phase A-G 已执行完成并回填。覆盖脱敏、LocalStorage/请求头、HLS 多轨/字幕/直播、DASH 断点、FFmpeg 错误收口、spider 拆分、计划状态校准。

```text
docs/plans/video-crawler-access-limited-sniffing-implementation-plan.md
```

访问受限嗅探计划 Phase A-E 已执行。覆盖访问受限分类、可视化嗅探、持久化 profile、响应正文候选提取和相关 UI/测试。

## 11. Git 和工作区注意事项

新对话开始后先做只读检查：

```powershell
git status --short -uall
git log --oneline -5
```

历史上曾出现 `.git` 对象异常和旧说明记录的 `fatal: bad object HEAD`，但最近检查时 `git status --short -uall` 没有输出。不要依据旧说明直接断定 Git 仍损坏，以当前命令输出为准。

开发注意：

- 可能存在用户未提交改动，不要重置或还原未确认的文件。
- 不要删除下载产物、断点续传 manifest、浏览器 profile、计划文档或测试源码。
- 阶段完成后可清理完全无用的测试临时目录和 `tools/video_crawler/**/__pycache__`。
- 清理前必须确认目标绝对路径位于项目目录内。

## 12. 新对话接手建议

如果继续开发视频爬虫：

1. 读取本文件。
2. 根据任务读取对应 `docs/plans/*.md`。
3. 读取相关源码和测试。
4. 先跑相关测试或完整测试。
5. 按计划做 TDD：先写失败测试，再实现，再回归。
6. 每个阶段结束后清理完全无用的临时文件。

如果继续维护视频爬虫，可直接给新对话这段提示：

```text
请读取 D:\Study\Projects\PythonProject\FireflyTools\docs\project-overview.md，
再根据任务读取 docs/plans/ 下的相关实施记录。
开始前检查 git status，不要还原用户改动。
```

## 13. 面向大模型的最短提示词

```text
请先读取 D:\Study\Projects\PythonProject\FireflyTools\docs\project-overview.md。
这是一个 PyQt6 媒体工具箱项目，重点模块是视频下载爬虫。
当前爬虫核心在 tools/video_crawler/，UI 队列入口在 tools/video_downloader.py。
开始前检查 git status，不要还原用户改动。
```
