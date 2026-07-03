"""把浏览器会话快照转换为媒体下载可安全继承的请求头。"""

from urllib.parse import urlparse

from tools.video_crawler.logging_utils import redact_for_display
from tools.video_crawler.models import BrowserSessionSnapshot


SENSITIVE_HEADER_NAMES = {"cookie", "authorization", "x-token", "x-auth-token"}

DOWNLOAD_HEADER_ALLOWLIST = {
    "authorization": "Authorization",
    "accept": "Accept",
    "accept-language": "Accept-Language",
    "range": "Range",
    "x-token": "X-Token",
    "x-auth-token": "X-Auth-Token",
}
# 不直接复制浏览器的全部 Header：Host、Content-Length、Sec-* 等字段
# 与新的 CDN 请求上下文不匹配。Cookie 也单独按目标域筛选，避免跨域泄漏。


def _cookie_matches_target(cookie: dict, target_host: str) -> bool:
    """判断浏览器 Cookie 的 Domain 范围是否覆盖目标媒体主机。"""
    domain = str(cookie.get("domain", "")).lstrip(".").lower()
    return bool(domain) and (
        target_host == domain or target_host.endswith("." + domain)
    )


def _cookie_header(cookies: tuple[dict, ...], target_url: str) -> str:
    """筛选适用 Cookie 并拼接为 HTTP Cookie 请求头。"""
    target_host = (urlparse(target_url).hostname or "").lower()
    pairs = []
    for cookie in cookies:
        if _cookie_matches_target(cookie, target_host):
            pairs.append(f"{cookie.get('name')}={cookie.get('value')}")
    return "; ".join(pairs)


def build_download_headers(
    base_headers: dict[str, str],
    snapshot: BrowserSessionSnapshot,
    target_url: str,
) -> dict[str, str]:
    """合并默认请求头与浏览器会话，生成媒体下载请求头。

    Header 使用白名单复制，Cookie 再按最终媒体 URL 的域名过滤。该顺序
    既保留防盗链所需的 Referer/Origin，也避免把页面会话泄漏给无关域。
    """
    headers = dict(base_headers)
    if snapshot.user_agent:
        headers["User-Agent"] = snapshot.user_agent
    if snapshot.referer:
        headers["Referer"] = snapshot.referer
    if snapshot.origin:
        headers["Origin"] = snapshot.origin

    for name, value in snapshot.headers.items():
        canonical = DOWNLOAD_HEADER_ALLOWLIST.get(name.lower(), name)
        if canonical in DOWNLOAD_HEADER_ALLOWLIST.values() and value:
            headers[canonical] = value

    cookie_value = _cookie_header(snapshot.cookies, target_url)
    if cookie_value:
        headers["Cookie"] = cookie_value
    return headers


def extract_download_request_headers(raw_headers: dict[str, str]) -> dict[str, str]:
    """从浏览器请求中提取允许继承到下载器的认证类 Header。"""
    extracted = {}
    for name, value in raw_headers.items():
        canonical = DOWNLOAD_HEADER_ALLOWLIST.get(name.lower())
        if canonical and value:
            extracted[canonical] = value
    return extracted


def redact_sensitive_text(text: str) -> str:
    """兼容旧调用方，将文本交给统一脱敏器处理。"""
    return redact_for_display(text)
