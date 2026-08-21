"""定义媒体候选、浏览器会话、页面诊断和嗅探配置数据模型。"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MediaKind(str, Enum):
    """枚举爬虫能够识别的媒体清单类型。"""

    #: 可通过单个 HTTP 请求直接获取的 MP4 文件。
    DIRECT_MP4 = "DIRECT_MP4"
    #: 由 M3U8 playlist 描述的 HTTP Live Streaming 媒体流。
    HLS = "HLS"
    #: 由 MPD 清单描述的 Dynamic Adaptive Streaming over HTTP 媒体流。
    DASH = "DASH"
    #: 检测到 Widevine、PlayReady、FairPlay 等数字版权保护特征的媒体。
    DRM = "DRM"
    #: 当前证据不足，无法归入内置下载器支持类型的媒体。
    UNKNOWN = "UNKNOWN"


# @dataclass 是 Python 提供的一个装饰器，用来快速定义“主要用来存数据的类”。
# frozen=True 表示这个数据类是“冻结的”,对象创建之后，不能重新给字段赋值。
# 禁止的是：把字段整体换掉 但它不一定禁止：修改字段内部的可变对象。
@dataclass(frozen=True)
class MediaCandidate:
    """描述一个待验证或待下载的媒体地址及其质量信息。"""

    #: 媒体文件或流媒体清单的绝对 URL。
    url: str
    #: 根据 URL、Content-Type 或响应内容识别出的媒体类型。
    kind: MediaKind
    #: 候选来源，例如 direct、network 或 response-body，用于判断证据可靠性。
    source: str
    #: 候选可信度分数，仅用于候选排序，不代表码率、分辨率或最终画质。
    score: int = 0
    #: 服务器响应的 Content-Type；无法取得时保留为空字符串。
    content_type: str = ""
    #: 媒体 playlist 中探测到的切片数量；尚未探测时为 None。
    segment_count: int | None = None
    #: 媒体流声明或根据 URL 推断出的带宽，单位通常为 bit/s。
    bandwidth: int | None = None
    #: 下载该候选是否可能需要继承浏览器 Cookie 或授权请求头。
    requires_session: bool = False
    #: 诊断阶段附加的不可变说明集合；每个实例使用独立的空 tuple。
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BrowserSessionSnapshot:
    """保存媒体请求需要继承的浏览器会话数据。"""

    #: 浏览器实际使用的 User-Agent，下载请求可复用它保持客户端身份一致。
    user_agent: str = ""
    #: 发起嗅探的页面 URL，可作为媒体请求的 Referer。
    referer: str = ""
    #: 页面来源，由协议、主机和端口组成，可作为媒体请求的 Origin。
    origin: str = ""
    #: Playwright 导出的 Cookie 字典；下载时只发送与目标域名匹配的项。
    cookies: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    #: 从真实媒体请求中筛选出的可继承请求头，每个快照使用独立字典。
    headers: dict[str, str] = field(default_factory=dict)  #: 当没有传入值时，自动创建一个新的默认对象。
    #: 页面 LocalStorage 快照，仅供诊断或后续扩展，不会自动转换成请求头。
    local_storage: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PageAccessSnapshot:
    """记录主页面访问结果和播放器相关 DOM 统计。"""

    #: 主文档响应的 HTTP 状态码；浏览器未取得响应对象时为 None。
    # | 表示类型联合，意思是：
    # status_code 可以是 int，也可以是 None。
    status_code: int | None = None
    #: 页面加载后的标题，用于辅助识别访问受限或验证页面。
    title: str = ""
    #: 页面经过重定向后最终停留的 URL。
    final_url: str = ""
    #: 当前主页面 DOM 中 video 元素的数量，仅作为播放器诊断线索。
    video_count: int = 0
    #: 当前主页面 DOM 中 iframe 元素的数量，用于判断播放器是否嵌在子页面。
    iframe_count: int = 0


@dataclass(frozen=True)
class SnifferOptions:
    """配置 Playwright 的可视化、持久会话和等待行为。"""

    #: 是否无界面运行 Chromium；False 时允许用户在浏览器中进行合法操作。
    headless: bool = True
    #: 是否复用磁盘中的 Chromium profile，以保留站点允许保存的会话状态。
    use_persistent_profile: bool = False
    #: 持久化 profile 的本地目录；其中可能含敏感会话信息，禁止提交 Git。
    profile_dir: str = "./browser_profiles/video_crawler"
    #: 等待播放器产生可靠媒体请求的最长秒数。
    manual_wait_seconds: int = 25

    # @property 是 Python 的一个装饰器，可以把一个方法变成“属性”来访问。
    @property
    def visible(self) -> bool:
        """返回嗅探器是否应显示浏览器窗口。"""
        return not self.headless


@dataclass(frozen=True)
class DiagnosticReport:
    """汇总候选媒体、浏览器会话、警告和错误。"""

    #: 本次诊断最初接收的网页或媒体 URL。
    source_url: str
    #: 按发现顺序保存的媒体候选；每个报告使用独立列表。
    candidates: list[MediaCandidate] = field(default_factory=list)
    #: 嗅探结束时捕获的浏览器会话；未启动浏览器时使用空快照。
    session: BrowserSessionSnapshot = field(default_factory=BrowserSessionSnapshot)
    #: 导航或早期页面诊断是否异常；为 True 时不能把空候选视为确定无媒体。
    navigation_incomplete: bool = False
    #: 不阻止继续分析、但需要向用户说明的诊断信息。
    warnings: list[str] = field(default_factory=list)
    #: 诊断阶段收集的错误文本；任务级失败优先使用 VideoDownloadError 表达。
    errors: list[str] = field(default_factory=list)

    @property
    def best_candidate(self) -> MediaCandidate | None:
        """按候选分数返回当前诊断报告中的最佳媒体地址。"""
        if not self.candidates:
            return None
        return sorted(self.candidates, key=lambda item: item.score, reverse=True)[0]

    @property
    def has_downloadable_candidate(self) -> bool:
        """判断最佳候选是否属于内置适配器支持的媒体类型。"""
        candidate = self.best_candidate
        return candidate is not None and candidate.kind in {
            MediaKind.DIRECT_MP4,
            MediaKind.HLS,
            MediaKind.DASH,
        }

    def to_user_summary(self) -> str:
        """把诊断报告转换为适合日志展示的简明文本。"""
        lines = [f"诊断 URL: {self.source_url}"]
        if self.candidates:
            lines.append(f"发现候选流: {len(self.candidates)} 个")
            for candidate in self.candidates:
                # 条件表达式:值1 if 条件 else 值2
                segment_text = (
                    f"，切片数 {candidate.segment_count}"
                    if candidate.segment_count is not None
                    else ""
                )
                lines.append(
                    f"- {candidate.kind.value}: {candidate.url}{segment_text}"
                )
        else:
            lines.append("未发现可下载的 MP4/M3U8/MPD 候选流。")
        lines.extend(f"警告: {warning}" for warning in self.warnings)
        lines.extend(f"错误: {error}" for error in self.errors)
        # 把列表里的每一项用换行符 \n 连接起来。
        return "\n".join(lines)
