# Video Crawler System Chrome Mode Design

## Goal

Add an optional experimental browser mode that lets the video crawler launch
the locally installed stable Google Chrome through Playwright. The mode is a
compatibility attempt for sites that reject Playwright's bundled Chromium; it
does not hide automation signals or promise to pass a site's security policy.

## Confirmed Context

The target video opens in an ordinary Chrome session on the same machine and
network. In the existing Playwright browser, the user can complete the visible
checkbox and a fresh `.kanav.ad` `cf_clearance` cookie is written, but the site
starts another verification cycle instead of opening the player. The current
profile was created by Chromium 145, while the installed stable Chrome is
version 151.

A local Playwright launch using the system Chrome channel still reports
`navigator.webdriver == true`. Therefore this mode may improve browser-version
or binary compatibility, but it is not an anti-detection bypass and may still
be rejected by the target site.

## User Interface

Add an unchecked `系统 Chrome（实验）` checkbox to the existing sniff-options
row beside `可视化嗅探` and `复用浏览器会话`.

- Default behavior remains Playwright's bundled Chromium.
- The option is available to both diagnosis and queued download tasks.
- The checkbox is independent of visible and persistent modes. For the target
  workflow, the user should select all three options.
- A tooltip and runtime log explain that the mode requires locally installed
  Google Chrome, still exposes Playwright automation, and may remain blocked.
- The program must not silently fall back to bundled Chromium when the user
  explicitly selects system Chrome. Launch failure should remain visible.

## Configuration Model

Extend `SnifferOptions` with:

- `use_system_chrome: bool = False`
- `system_chrome_profile_dir: str = "./browser_profiles/video_crawler_chrome"`

Expose small read-only properties for the active Playwright channel and active
profile directory. The existing bundled-Chromium profile remains
`./browser_profiles/video_crawler`.

When persistent mode and system Chrome are both enabled, use the separate
`video_crawler_chrome` profile. Never open the existing Chromium profile with
the newer system Chrome, because Chrome may migrate it and make it incompatible
with the bundled browser.

## Browser Launching

`PageSniffer._launch_context` will construct one launch configuration:

- default mode: omit `channel`, preserving bundled Chromium;
- system Chrome mode: pass `channel="chrome"` to `chromium.launch` or
  `chromium.launch_persistent_context`;
- persistent mode: pass the active profile directory selected by the options;
- keep the existing locale, timezone, viewport, headers, headless selection,
  and `--mute-audio` argument unchanged.

No `--disable-blink-features`, `navigator.webdriver` override, stealth plugin,
canvas/WebGL spoofing, CDP attachment to the user's daily browser, or cookie
copying from the normal Chrome profile is included.

## Data Flow

The UI option must follow the same snapshot semantics as the existing sniffer
settings:

1. `_build_sniffer_options` reads the checkbox for diagnosis.
2. `add_to_queue` stores `sniffer_use_system_chrome` in each task snapshot.
3. `_execute_task` reconstructs `SnifferOptions` from the task snapshot.
4. Legacy queued tasks without the new field default to bundled Chromium.
5. `PageSniffer` logs the selected browser mode before launch.

## Error Handling

If the Chrome channel is unavailable or cannot start, preserve the Playwright
launch error and add a concise Chinese log explaining that Google Chrome must
be installed. Do not silently change browser mode, profile, or verification
behavior.

If system Chrome still loops on the verification page, the existing visible
challenge wait expires with `HTTP_FORBIDDEN`. The error text should make clear
that system Chrome is experimental and does not bypass site policy.

## Testing

Add deterministic tests before production changes:

- `SnifferOptions` defaults to bundled Chromium and the existing profile.
- System Chrome options select Playwright channel `chrome` and the independent
  `video_crawler_chrome` profile.
- Non-persistent system Chrome launch passes `channel="chrome"` to
  `chromium.launch`.
- Persistent system Chrome launch passes the independent profile path and
  `channel="chrome"` to `launch_persistent_context`.
- The new checkbox defaults off.
- Diagnosis options, queue snapshots, worker reconstruction, and legacy task
  defaults carry the new setting correctly.
- Existing visible challenge waiting, session reuse, navigation recovery, and
  downloader tests remain green.

The target site remains a manual validation because its security decisions are
external and nondeterministic.

## Files in Scope

- `tools/video_crawler/models.py`
- `tools/video_crawler/sniffer.py`
- `tools/video_downloader.py`
- `tests/test_video_crawler_sniffer_access.py`
- `tests/test_video_downloader.py`
- `docs/项目介绍.md`

No changes to DRM handling, media selection, download adapters, automation
fingerprint hiding, or ordinary Chrome user data are included.

## Acceptance Criteria

- Existing users see no behavior change while the option is unchecked.
- Selecting system Chrome causes Playwright to use the installed Chrome channel.
- Selecting system Chrome plus persistent session uses a separate local profile.
- Diagnosis and queued downloads use the same option value.
- Legacy queued tasks remain compatible and use bundled Chromium.
- Launch logs clearly label the mode as experimental and non-bypassing.
- Automated focused and full test suites pass.
