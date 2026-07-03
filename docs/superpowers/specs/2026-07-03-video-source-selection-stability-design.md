# Video Source Selection Stability Design

## Goal

Make webpage sniffing deterministic when advertising or recommendation MP4 files
arrive before the main HLS stream. The downloader must keep enough observation
time for the preferred stream, avoid selecting an arbitrary response-body URL,
and preserve the existing structured HLS validation errors.

## Root Cause

The current sniffer navigates with `domcontentloaded` and ends its explicit wait
as soon as any network MP4, HLS, or DASH candidate exists. On pages that request
a short MP4 before starting the real player, this closes Chromium before the HLS
request is emitted. The spider then walks candidates in discovery order and
returns the first MP4, even when it came from an HTML, JSON, or script response
body and has weaker evidence than a network request.

The earlier standalone script appeared more reliable because `networkidle`
usually timed out after 25 seconds while the response listener remained active.
That accidental observation window captured the delayed HLS stream. Restoring
its fallback to the last unverified M3U8 URL would reintroduce random downloads
and is outside this design.

## Considered Approaches

### 1. Restore `networkidle`

This closely reproduces the old script, but busy pages routinely never become
idle. It makes a navigation timeout part of normal control flow and adds a fixed
25-second delay. It is useful historical evidence, not a stable policy.

### 2. Type-aware explicit observation and validated ranking (recommended)

Keep `domcontentloaded`, then use the configured sniff wait as an explicit
post-navigation observation budget. A network HLS request may finish the wait
early. MP4, DASH, and response-body candidates do not end the wait; they remain
fallback evidence while the sniffer waits for a possible HLS request. Normalize
and validate candidates before the spider chooses one.

This directly fixes the race, keeps the maximum wait user-configurable, and
retains the project's current HLS-first architecture.

### 3. Keep the current wait and only improve MP4 scoring

Content length and source quality can reduce bad MP4 choices, but they cannot
recover an HLS request that was never observed. This is useful defense in depth
but insufficient as the primary fix.

## Sniffer Wait Policy

- Keep `page.goto(..., wait_until="domcontentloaded")` so access diagnostics can
  inspect the main response without treating a busy page as a navigation error.
- Start the explicit observation loop after the playback click attempt.
- Treat a network HLS candidate as terminal evidence and allow an early exit.
- Do not let network MP4, network DASH, or any response-body candidate terminate
  the loop. If no network HLS appears, observe for the full
  `SnifferOptions.manual_wait_seconds` budget before using fallbacks.
- Candidates captured before the click remain available, but a preloaded MP4
  cannot make the post-click observation loop return immediately.
- Log whether waiting ended because a network HLS arrived or because the budget
  expired, together with candidate counts by kind and source.

The default budget remains 10 seconds. No new UI setting is required.

## Candidate Normalization

Before selection, deduplicate candidates by exact URL while preserving the first
discovery position. When a URL is first extracted from a response body and later
seen as a network response, replace its evidence with the network source,
content type, and higher score. This prevents duplicate entries and ensures a
real request outranks a textual mention of the same resource.

Response-body HLS candidates remain eligible for the existing playlist probe.
Response-body MP4 candidates remain weak fallbacks and must pass an HTTP metadata
probe before download.

## Selection Policy

The spider must group candidates before choosing; it must not return from one
discovery-order loop.

1. Probe all HLS candidates with the existing segment and quality logic. Return
   the best verified HLS candidate.
2. If HLS candidates exist but all fail verification, keep the current
   `NETWORK_TIMEOUT` or `M3U8_PARSE_FAILED` error. Do not silently fall through
   to MP4.
3. If there is no HLS candidate, rank MP4 candidates independently of DASH.
   Prefer network evidence over response-body evidence, then prefer successfully
   probed larger resources, then score, then original discovery order.
4. Probe MP4 metadata with `HEAD`; when HEAD is unsupported or lacks a useful
   total size, retry with a streamed `GET` carrying `Range: bytes=0-0`. Parse
   `Content-Range` before `Content-Length` and close the response without reading
   the body. Use browser-derived headers for the candidate host.
5. Do not impose a fixed minimum duration or size because legitimate videos can
   be short. Size is comparative evidence, not a rejection threshold.
6. A response-body-only MP4 is eligible only when its metadata probe succeeds.
   If no MP4 candidate can be validated, continue to DASH or return the existing
   no-media error.
7. Preserve the current overall type preference: verified HLS, validated MP4,
   then DASH.

## Error Handling and Logging

- MP4 metadata probe failures are recorded per candidate and do not abort other
  probes.
- Logs identify candidate kind, source, deduplication upgrades, metadata size,
  the wait-stop reason, and the final selection reason. URLs continue through
  the existing redaction layer.
- HLS failures retain their structured error codes and retryability semantics.
- No DRM, access-control, profile, HLS-download, or segment-tolerance behavior
  changes are included.

## Testing

Add deterministic tests before implementation:

- A network MP4 does not stop waiting before the observation budget expires.
- A delayed network HLS stops waiting and wins over earlier MP4 candidates.
- Response-body candidates never stop waiting.
- A response-body MP4 followed by the same network MP4 is deduplicated and
  upgraded to network evidence.
- Candidate order `DASH, response-body MP4, network MP4` still selects the
  validated network MP4 according to type and source policy.
- Among equally sourced validated MP4 candidates, the larger resource wins.
- A response-body-only MP4 with a failed metadata probe is not downloaded.
- Existing HLS validation, session inheritance, access diagnostics, and queue
  tests remain green.

Use fakes for timing and HTTP metadata responses. Keep the live KanAV URL as a
manual regression check rather than a permanent automated test because remote
content and availability are nondeterministic.

## Files in Scope

- `tools/video_crawler/sniffer.py`
- `tools/video_crawler/spider.py`
- `tests/test_video_crawler_sniffer_access.py`
- `tests/test_video_downloader.py`

The existing uncommitted Chinese documentation changes in these files must be
preserved and extended rather than replaced.

## Acceptance Criteria

- The event order “advertising MP4 first, main HLS later” selects the HLS stream.
- Increasing the configured sniff wait genuinely increases the observation
  window when only fallback candidates exist.
- No candidate is selected solely because it appeared first in response text.
- The live target can still identify
  `https://cdn16.11yun.space/GAV1/328752/328752.m3u8` when that request is emitted
  within the configured observation budget.
- Focused tests and the complete test suite pass, apart from separately reported
  pre-existing filesystem permission residue if it recurs.
