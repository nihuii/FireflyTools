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


def _cookie_matches_target(cookie: dict, target_host: str) -> bool:
    domain = str(cookie.get("domain", "")).lstrip(".").lower()
    return bool(domain) and (
        target_host == domain or target_host.endswith("." + domain)
    )


def _cookie_header(cookies: tuple[dict, ...], target_url: str) -> str:
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
    extracted = {}
    for name, value in raw_headers.items():
        canonical = DOWNLOAD_HEADER_ALLOWLIST.get(name.lower())
        if canonical and value:
            extracted[canonical] = value
    return extracted


def redact_sensitive_text(text: str) -> str:
    return redact_for_display(text)
