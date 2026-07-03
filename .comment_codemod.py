"""一次性为 FireflyTools 运行时代码插入中文 docstring。"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / "tools"

MODULE_DOCS = {
    "tools/__init__.py": "初始化 tools 包，并让子进程优先使用仓库内置的 FFmpeg。",
    "tools/runtime_setup.py": "配置 FireflyTools 运行时依赖的本地可执行工具。",
    "tools/main.py": "构建 FireflyTools 主窗口、壁纸画布、自定义标题栏和工具标签页。",
    "tools/theme_utils.py": "根据壁纸颜色生成 PyQt6 全局主题，并提供通用阴影效果。",
    "tools/video_downloader.py": "实现视频下载爬虫的 PyQt6 界面、任务队列和批次结果反馈。",
    "tools/video_extractor.py": "提供把子目录视频移动到根目录的图形化批处理工具。",
    "tools/keyword_organizer.py": "提供按文件名关键字归档图片和视频的图形化工具。",
    "tools/image_resizer.py": "提供批量图片缩放、裁剪和格式转换的 PyQt6 工具。",
    "tools/video_crawler/__init__.py": "公开视频爬虫包的核心异常类型。",
    "tools/video_crawler/models.py": "定义媒体候选、浏览器会话、页面诊断和嗅探配置数据模型。",
    "tools/video_crawler/errors.py": "定义视频下载流程使用的结构化错误码和异常。",
    "tools/video_crawler/logging_utils.py": "清理日志中的认证信息和敏感 URL 查询参数。",
    "tools/video_crawler/reporting.py": "把结构化视频诊断报告格式化为经过脱敏的用户文本。",
    "tools/video_crawler/session.py": "把浏览器会话快照转换为媒体下载可安全继承的请求头。",
    "tools/video_crawler/resume.py": "持久化分片下载状态，为 HLS 和 DASH 提供断点续传判断。",
    "tools/video_crawler/diagnostics.py": "按 URL 类型执行静态分析或网页嗅探，并生成统一诊断报告。",
    "tools/video_crawler/sniffer.py": "使用 Playwright 诊断网页访问状态并嗅探真实媒体请求。",
    "tools/video_crawler/spider.py": "编排网页嗅探、候选流选择和 MP4/HLS/DASH 下载适配器。",
    "tools/video_crawler/adapters/__init__.py": "提供视频下载适配器包。",
    "tools/video_crawler/adapters/base.py": "定义下载适配器协议，并按优先级选择可处理候选的适配器。",
    "tools/video_crawler/adapters/direct_mp4.py": "实现直接 MP4 媒体流的分块下载与输出校验。",
    "tools/video_crawler/adapters/ytdlp.py": "封装可选的 yt-dlp 外部后备下载流程。",
    "tools/video_crawler/adapters/dash.py": "解析静态 DASH MPD，下载音视频分片并使用 FFmpeg 合并。",
    "tools/video_crawler/adapters/hls.py": "下载和合并 HLS 点播、直播、加密、多轨及 fMP4 媒体流。",
}

CLASS_DOCS = {
    "BgWidget": "在圆角窗口内按比例绘制当前壁纸的底层画布。",
    "CustomTitleBar": "实现无边框主窗口的拖动、缩放状态切换和标题栏按钮。",
    "MediaToolboxApp": "组装四个媒体工具标签页并管理全局壁纸与窗口尺寸。",
    "VideoDownloaderTool": "管理视频下载表单、任务队列、后台执行和批次结果展示。",
    "VideoExtractorTool": "扫描目录树并把视频文件安全移动到所选根目录。",
    "KeywordOrganizerTool": "根据关键字把支持的媒体文件移动到对应归档目录。",
    "SmartImageResizerTool": "批量读取图片并按目标尺寸和重心策略进行裁剪。",
    "MediaKind": "枚举爬虫能够识别的媒体清单类型。",
    "MediaCandidate": "描述一个待验证或待下载的媒体地址及其质量信息。",
    "BrowserSessionSnapshot": "保存媒体请求需要继承的浏览器会话数据。",
    "PageAccessSnapshot": "记录主页面访问结果和播放器相关 DOM 统计。",
    "SnifferOptions": "配置 Playwright 的可视化、持久会话和等待行为。",
    "DiagnosticReport": "汇总候选媒体、浏览器会话、警告和错误。",
    "VideoErrorCode": "枚举可向 UI 和批处理结果稳定传递的错误类别。",
    "VideoDownloadError": "携带错误码、重试建议和内部详情的视频下载异常。",
    "SegmentManifest": "维护已完成分片的大小信息并将其原子化写入磁盘。",
    "VideoDiagnosticService": "在静态 URL 分析和 Playwright 页面嗅探之间进行路由。",
    "PageSniffer": "驱动 Playwright 页面并收集媒体候选与浏览器会话。",
    "UniversalVideoSpider": "协调候选解析、正片选择、适配器下载和输出校验。",
    "VideoAdapter": "约束所有内置视频下载适配器必须实现的接口。",
    "VideoDownloadOrchestrator": "按适配器优先级选择实现并执行下载。",
    "DirectMp4Adapter": "处理无需清单解析的直接 MP4 响应。",
    "YtDlpAdapter": "在用户明确启用时调用 yt-dlp 作为外部后备。",
    "MpdCapabilities": "描述 MPD 的直播、DRM、音轨和视频轨能力。",
    "DashTrackPlan": "描述单条 DASH 音频或视频轨的初始化段与媒体段。",
    "DashDownloadPlan": "组合 DASH 下载所需的视频轨和音频轨计划。",
    "DashAdapter": "处理无 DRM 静态 DASH 清单的下载、续传和合并。",
    "HlsRenditionPlan": "保存 HLS 主清单选择出的默认音轨和字幕轨。",
    "HlsAdapter": "处理 HLS 清单解析、分片下载、解密、多轨和 FFmpeg 合并。",
}

FUNCTION_DOCS = {
    "__init__": "初始化实例需要的状态、依赖和运行资源。",
    "paintEvent": "在控件重绘时裁剪圆角区域并铺满当前壁纸。",
    "toggle_maximize": "在最大化和普通窗口状态之间切换并同步按钮图标。",
    "mouseDoubleClickEvent": "双击标题栏时切换主窗口最大化状态。",
    "mousePressEvent": "记录无边框窗口开始拖动时的全局鼠标位置。",
    "mouseMoveEvent": "根据鼠标位移移动未最大化的主窗口。",
    "mouseReleaseEvent": "结束标题栏拖动并清除上一次鼠标位置。",
    "load_wallpapers": "从项目 pic 目录收集可用的 JPG 和 PNG 壁纸。",
    "switch_wallpaper": "循环切换到下一张壁纸并刷新主题。",
    "apply_wallpaper": "应用当前壁纸，同时重新生成全局样式表。",
    "resizeEvent": "窗口尺寸变化后重新定位右下角缩放手柄。",
    "_position_size_grip": "把缩放手柄固定到背景画布右下角。",
    "configure_bundled_ffmpeg": "在 Windows 下把仓库内 FFmpeg 目录置于 PATH 首位并返回可执行文件。",
    "get_dominant_color": "缩小壁纸并统计主色，用作动态主题的颜色来源。",
    "is_color_dark": "根据感知亮度判断颜色是否适合使用浅色前景。",
    "adjust_color": "按给定比例调亮或调暗 RGB 颜色。",
    "apply_shadow": "为指定控件安装统一的柔和投影效果。",
    "get_global_stylesheet": "根据壁纸主色生成覆盖主窗口和常用控件的 QSS。",
    "select_folder": "打开目录选择器并把用户选择写回输入框。",
    "choose_output_dir": "选择图片处理结果的输出目录。",
    "select_files": "选择待处理图片并在界面中更新文件计数。",
    "start_processing": "校验输入后启动后台批处理线程，避免阻塞 Qt 事件循环。",
    "append_log": "把经过处理的消息追加到日志控件并滚动到底部。",
    "update_btn": "根据当前输入是否完整更新执行按钮的可用状态。",
    "extract_task": "在后台扫描目录、移动视频并按选项清理空目录。",
    "diagnose_current_url": "校验 URL 后启动独立线程执行媒体诊断。",
    "_diagnose_task": "使用当前嗅探设置生成诊断报告并发送到 UI 日志。",
    "add_to_queue": "校验表单并把当前所有下载设置快照为独立任务。",
    "_enqueue_task": "复制任务、放入线程安全队列并更新队列列表。",
    "_build_sniffer_options": "把当前嗅探控件值转换为不可变配置对象。",
    "_execute_task": "执行单个队列任务，并把异常统一转换为结构化结果字典。",
    "_should_use_ytdlp_fallback": "判断内置失败是否满足用户启用的 yt-dlp 后备条件。",
    "_execute_ytdlp_fallback": "调用 yt-dlp 后备适配器并返回与内置流程一致的结果。",
    "_finish_task": "记录任务结果、维护队列计数并在批次结束时发射信号。",
    "retry_failed_tasks": "按原始配置重新入队所有标记为可重试的失败任务。",
    "has_retryable_failures": "判断批次结果中是否至少包含一个可重试失败。",
    "format_batch_results": "生成批次成功、失败、错误统计和重试建议文本。",
    "_format_success_line": "格式化单个成功任务，并标注实际使用的下载引擎。",
    "show_batch_results": "显示批次汇总对话框，并按结果决定是否提供重试按钮。",
    "queue_worker": "持续消费线程安全队列，在后台顺序执行下载任务。",
    "visible": "返回嗅探器是否应显示浏览器窗口。",
    "best_candidate": "按候选分数返回当前诊断报告中的最佳媒体地址。",
    "has_downloadable_candidate": "判断最佳候选是否属于内置适配器支持的媒体类型。",
    "to_user_summary": "把诊断报告转换为适合日志展示的简明文本。",
    "redact_for_display": "隐藏 Header 和 URL 中的认证令牌后返回可展示文本。",
    "format_diagnostic_report": "格式化并脱敏诊断报告的候选、警告和错误。",
    "_cookie_matches_target": "判断浏览器 Cookie 的域和路径是否适用于目标媒体 URL。",
    "_cookie_header": "筛选适用 Cookie 并拼接为 HTTP Cookie 请求头。",
    "build_download_headers": "合并默认请求头与浏览器会话，生成媒体下载请求头。",
    "extract_download_request_headers": "从浏览器请求中提取允许继承到下载器的认证类 Header。",
    "redact_sensitive_text": "兼容旧调用方，将文本交给统一脱敏器处理。",
    "load": "从磁盘读取断点清单；文件缺失或损坏时回退为空状态。",
    "save": "通过临时文件替换方式写入当前分片状态，避免半写入 JSON。",
    "mark_downloaded": "记录指定分片已经完成以及对应文件大小。",
    "is_downloaded": "同时校验清单记录、文件存在性和大小，判断分片能否复用。",
    "analyze_static_url": "根据直链后缀生成无需联网嗅探的基础诊断报告。",
    "analyze_mpd_text": "解析 MPD 文本并报告 DASH 支持范围或 DRM 风险。",
    "analyze": "优先处理已知直链类型，其余 URL 交给页面嗅探器。",
    "_normalize_escaped_url_text": "还原 JSON 风格 URL 转义，同时保留正则反斜杠供后续过滤。",
    "_is_probable_media_url": "排除正则占位符并确认 URL 具有可识别媒体后缀。",
    "merge_media_request_headers": "从媒体请求中增量合并允许继承的请求头。",
    "classify_media_response": "综合响应 URL 与 content-type 构造媒体候选。",
    "extract_media_urls_from_text": "从 JSON、HTML 或脚本文本提取并去重媒体绝对地址。",
    "candidates_from_response_text": "把响应正文中发现的 URL 转换为带来源的候选对象。",
    "should_continue_waiting_for_media": "根据候选是否出现和等待上限决定是否继续轮询。",
    "has_reliable_media_candidate": "仅把网络层或直接清单候选视为可提前结束等待的证据。",
    "detect_access_limited_page": "依据状态码和页面标题识别访问受限并构造结构化异常。",
    "_launch_context": "按配置创建普通浏览器上下文或持久化 Chromium 上下文。",
    "sniff": "打开目标页面，收集媒体候选与会话，并返回结构化诊断报告。",
    "handle_response": "处理单个 Playwright 响应，捕获网络候选并按需读取正文。",
    "wait_for_candidates": "按可靠候选策略轮询页面，支持可视化人工操作窗口。",
    "is_timeout_like_error": "沿异常链判断错误是否代表网络超时或 Windows 10060。",
    "_resolution_tuple": "把分辨率对象安全转换为可排序的宽高整数元组。",
    "_resolution_pixels": "计算分辨率总像素，用于候选画质排序。",
    "_infer_bandwidth_from_url": "从 URL 中的 3000k 等片段推断近似带宽。",
    "_best_variant_info": "从 HLS 主清单选择带宽和分辨率最高的变体信息。",
    "_format_quality_metrics": "把带宽和分辨率格式化为便于诊断的日志片段。",
    "_build_orchestrator": "构造内置适配器并按优先级注册到下载编排器。",
    "_hls_adapter": "创建继承当前并发、会话、续传和直播设置的 HLS 适配器。",
    "_classify_direct_url": "根据 URL 后缀快速构造 MP4、HLS 或 DASH 直链候选。",
    "_resolve_candidate": "解析直链或网页，返回最终候选及嗅探到的浏览器会话。",
    "run": "执行一次完整下载，并在返回前验证输出文件非空。",
    "_verify_output": "检查输出路径是否存在且文件大小大于零。",
    "_select_best_m3u8": "探测候选切片与画质，并在正片容差内选择最佳 HLS 地址。",
    "_sniff_real_url": "运行页面嗅探，并在多个候选之间完成正片决策。",
    "_download_mp4": "通过直接 MP4 适配器下载指定地址。",
    "_download_ts": "兼容旧调用方，将单个 HLS 分片交给适配器下载。",
    "_normalize_download_item": "把旧式 URL 字符串或分片字典规范为统一下载项。",
    "_download_segments": "兼容旧测试入口，委托 HLS 适配器并发下载分片。",
    "_build_segment_cipher": "兼容旧入口，构造指定 HLS 分片使用的解密器。",
    "_download_m3u8": "通过 HLS 适配器下载清单并返回最终 MP4 路径。",
    "_merge_with_ffmpeg": "兼容旧入口，委托 HLS 适配器执行 FFmpeg 合并。",
    "select_adapter": "按优先级返回第一个声明可处理候选的适配器。",
    "_strip_namespace": "移除 XML 标签的命名空间前缀以便统一比较。",
    "_children_named": "返回元素下所有指定本地标签名的直接子节点。",
    "_first_child_named": "返回第一个指定名称的子节点，不存在时返回空值。",
    "_base_url": "读取元素 BaseURL，并相对父级 URL 解析为绝对地址。",
    "_parse_iso8601_duration_seconds": "解析 MPD 常见 ISO-8601 时长为秒数。",
    "parse_mpd_capabilities": "检查 MPD 的直播、DRM 以及音视频轨能力。",
    "_adaptation_kind": "根据 contentType、mimeType 和 Representation 推断轨道类型。",
    "_best_representation": "按 bandwidth 选择 AdaptationSet 中质量最高的表示。",
    "_segment_template_for": "按 Representation 优先级查找可继承的 SegmentTemplate。",
    "_track_plan": "展开单条 DASH 轨道的初始化段和媒体段 URL。",
    "expand": "替换 SegmentTemplate 中的表示 ID、编号和格式化占位符。",
    "build_static_segment_template_plan": "为受支持的静态 MPD 构造音视频下载计划。",
    "fetch_url": "使用当前会话请求 URL，并把 HTTP 错误转换为结构化异常。",
    "_write_track": "按 manifest 复用已完成分片并顺序写出完整轨道文件。",
    "_mux_tracks": "调用 FFmpeg 将 DASH 视频轨和音频轨无重编码封装为 MP4。",
    "_is_default_rendition": "判断 HLS 扩展媒体条目是否标记为默认轨道。",
    "build_hls_rendition_plan": "从主清单选出默认音轨和字幕轨 URI。",
    "derive_hls_iv": "优先使用显式 IV，否则按媒体序号生成 16 字节大端 IV。",
    "parse_hls_byterange": "解析 HLS BYTERANGE 长度和可选起始偏移。",
    "byterange_end": "计算当前字节区间的闭区间结束位置。",
    "group_fmp4_segments": "按 init map 和 discontinuity 把 fMP4 分片划分为合并组。",
    "ordered_ts_segments": "保持原清单顺序返回传统 TS 分片列表。",
    "is_live_playlist": "通过 ENDLIST 状态判断清单是否仍在滚动更新。",
    "headers": "返回当前下载会话请求头的防御性副本。",
    "_run_async": "在同步调用边界运行协程，并处理已有事件循环场景。",
    "_load_playlist": "加载 HLS 清单，并将超时和解析失败映射为结构化错误。",
    "_remove_temp_file": "尽力删除临时文件，清理失败只记录警告。",
    "_validate_segment_failures": "计算失败比例，超过容忍阈值时阻止不完整合并。",
    "download_ts": "带重试、退避、Range 和可选 AES 解密地下载单个分片。",
    "normalize_download_item": "规范分片字典并解析绝对 URL、范围和媒体序号。",
    "download_segments": "使用信号量限制并发，下载分片并持续更新续传清单。",
    "bounded_download": "在信号量保护范围内下载单项并收集失败信息。",
    "build_segment_cipher": "读取 HLS 密钥并按分片序号创建 AES-CBC 解密器。",
    "download_url": "下载较小的密钥或初始化资源，并保留当前会话请求头。",
    "download_media_playlist": "下载单个媒体清单并合并为临时 MP4。",
    "download_master_with_renditions": "下载主视频流及默认音轨/字幕，再执行最终封装。",
    "mux_renditions": "调用 FFmpeg 把视频、可选音频和字幕封装为目标 MP4。",
    "download_subtitle_playlist": "下载 WebVTT 字幕分片并拼接为单个字幕文件。",
    "download_live_playlist": "在录制窗口内轮询直播清单并去重收集新分片。",
    "download_collected_live_segments": "下载已收集的直播分片并合并输出。",
    "_download_playlist_to_mp4": "下载点播媒体清单、复用断点分片并合并为 MP4。",
    "merge_with_ffmpeg": "按 TS 或 fMP4 路径调用 FFmpeg 修复并生成最终容器。",
    "is_available": "判断用户开关和系统 yt-dlp 可执行文件是否同时可用。",
    "_build_command": "构造不经 shell 展开的 yt-dlp 参数列表。",
    "_find_output_path": "根据输出模板查找 yt-dlp 实际生成的非临时文件。",
    "_is_nonempty_file": "判断路径是否指向存在且非空的普通文件。",
}


def _docstring_lines(text: str, indent: str) -> list[str]:
    """把说明文本转换为保持给定缩进的三引号字符串行。"""
    return [f'{indent}"""{text}"""\n']


def _iter_definitions(node: ast.AST, parents: tuple[str, ...] = ()):
    """递归产出定义节点及其限定名称。"""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = ".".join((*parents, child.name))
            yield child, qualname
            yield from _iter_definitions(child, (*parents, child.name))
        elif not isinstance(child, (ast.expr, ast.stmt)):
            yield from _iter_definitions(child, parents)


def _definition_doc(node: ast.AST, qualname: str) -> str:
    """按定义类型和名称返回对应中文说明。"""
    if isinstance(node, ast.ClassDef):
        return CLASS_DOCS.get(node.name, f"封装 {node.name} 的状态和协作行为。")
    return FUNCTION_DOCS.get(
        node.name,
        f"执行 {qualname} 所负责的内部处理。",
    )


def document_file(path: Path) -> list[str]:
    """在不改变原有语句顺序的前提下插入缺失 docstring。"""
    relative = path.relative_to(ROOT).as_posix()
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source, filename=str(path))
    insertions: list[tuple[int, list[str]]] = []
    fallbacks: list[str] = []

    if not ast.get_docstring(tree):
        module_doc = MODULE_DOCS[relative]
        insertions.append((0, [f'"""{module_doc}"""\n', "\n"]))

    for node, qualname in _iter_definitions(tree):
        if ast.get_docstring(node):
            continue
        first_statement = node.body[0]
        indent = " " * first_statement.col_offset
        text = _definition_doc(node, qualname)
        if text.startswith("执行 ") and text.endswith(" 所负责的内部处理。"):
            fallbacks.append(f"{relative}:{qualname}")
        insertions.append(
            (first_statement.lineno - 1, _docstring_lines(text, indent))
        )

    for index, new_lines in sorted(insertions, reverse=True):
        lines[index:index] = new_lines
    path.write_text("".join(lines), encoding="utf-8", newline="")
    return fallbacks


def main() -> None:
    """处理全部运行时 Python 文件，并报告使用兜底说明的定义。"""
    fallbacks = []
    for path in sorted(TOOLS.rglob("*.py")):
        fallbacks.extend(document_file(path))
    if fallbacks:
        print("Fallback docstrings:")
        print("\n".join(fallbacks))


if __name__ == "__main__":
    main()
