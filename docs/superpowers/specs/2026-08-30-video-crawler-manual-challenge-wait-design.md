# Video Crawler Manual Challenge Wait Design

## Goal

Allow visible Playwright sniffing to remain open while a user completes a
site-provided security verification, then continue into the real player and
capture its media requests. Preserve immediate structured failure for headless
jobs and do not add automated challenge bypass or browser-fingerprint evasion.

## Root Cause

The UI correctly passes the visible, persistent-profile, and wait-duration
options into `PageSniffer`. The failure is inside the sniffer control flow.

After `page.goto`, the sniffer immediately creates an access snapshot. If the
first document response is HTTP 403, `detect_access_limited_page` raises
`HTTP_FORBIDDEN` before playback triggering and before `wait_for_candidates`.
The surrounding `finally` block then closes the browser context. Consequently,
the configured wait duration is never used on a verification page, the real
page can appear only briefly during a verification redirect, and the persistent
profile cannot finish recording a successfully verified session.

The existing unit test encodes this immediate-failure behavior for every mode,
so the repair requires a mode-aware test and control-flow change rather than a
larger timeout alone.

## Considered Approaches

### 1. Increase the existing sniff wait

This does not work because the exception is raised before the wait loop starts.
It changes the UI value but not the failing execution path.

### 2. Add a separate session-preparation window

A dedicated button could open the persistent browser profile before queueing a
download. This would provide a clear manual workflow, but it introduces new UI
and browser-lifecycle state even though the existing visible sniff operation
already has an appropriate wait setting.

### 3. Defer access failure during visible sniffing (recommended)

Keep the current immediate failure in headless mode. In visible mode, treat the
initial access error as provisional, keep the same context open until the
configured deadline, and poll current page state. Once the verification page
has transitioned to the real page, trigger playback and continue observing
responses for the remainder of the same deadline.

This is the smallest change that makes the current controls behave as described
and preserves the project's structured access-error handling.

## Access-State Recognition

Keep HTTP 403 and the existing access-denied title markers as access-limited
evidence. Extend title recognition to the verification states shown by the
target site, including normalized forms of:

- `Just a moment`
- `Checking your browser`
- `正在进行安全验证`
- `安全验证`

Title matching will be case-insensitive and tolerant of whitespace and common
punctuation differences. This is diagnostic recognition only; it will not
interact with, solve, or bypass the verification widget.

The initial navigation status is evidence about the initial document only. It
must not permanently classify the tab after the page has navigated or refreshed
to a real player. During visible waiting, current title, URL, player DOM, and
new main-document responses are resampled rather than reusing the stale first
403 snapshot as the final decision.

## Sniffer Control Flow

Use one monotonic observation deadline per sniff operation after the initial
navigation. Verification waiting and media waiting share this budget, so a
60-second setting means at most roughly 60 seconds of post-navigation waiting,
not 60 seconds for verification plus another 60 seconds for media.

The flow is:

1. Attach response listeners before navigation and retain normal media
   candidate collection.
2. Navigate and capture the initial access snapshot.
3. If the snapshot is access-limited in headless mode, raise the existing
   non-retryable `HTTP_FORBIDDEN` immediately.
4. If it is access-limited in visible mode, log that manual verification is
   being awaited and do not trigger a center click on the challenge page.
5. Poll at short intervals until one of these conditions occurs:
   - the page is no longer access-limited;
   - a reliable network HLS candidate is captured;
   - the observation deadline expires.
6. When the page first becomes accessible, log the transition, trigger the
   player once, and use the remaining budget to observe media requests.
7. If a reliable network HLS candidate appears, finish observation early using
   the existing candidate policy.
8. At the deadline, take a final access snapshot. Raise `HTTP_FORBIDDEN` only
   when the page is still recognizably restricted and no reliable media request
   proved that the player was reached.
9. If verification cleared but no media was found, return the diagnostic report
   normally so the existing `NO_MEDIA_FOUND` path gives the accurate failure.

The context remains open throughout this process. The existing final session
capture then persists allowed cookies and browser storage before closing the
context.

## Logging and Errors

Add concise log states for:

- initial verification page detected and manual completion awaited;
- verification cleared and playback triggering started;
- verification wait expired;
- final page status/title/player counts used for access classification.

Continue redacting URLs and exception text through the existing display helper.
The final access error remains `HTTP_FORBIDDEN`, non-retryable, and includes the
final diagnostic snapshot. A cleared verification page with no stream must not
be mislabeled as forbidden.

## Testing

Add deterministic fake-page tests before implementation:

- Headless sniffing still raises immediately on an initial HTTP 403.
- Visible sniffing does not raise immediately and consumes the configured wait
  while a verification page remains present.
- A visible page can transition from initial 403/`Just a moment` to a normal
  player, trigger playback, emit a delayed network HLS response, and return a
  successful diagnostic report.
- A verification page that clears but emits no media reaches the normal
  no-candidate report instead of `HTTP_FORBIDDEN`.
- A verification page still present at the deadline raises `HTTP_FORBIDDEN`
  using its final snapshot.
- English and Chinese verification titles are recognized after normalization.
- The existing navigation-timeout, candidate waiting, iframe playback,
  persistent-profile wiring, and queue-result tests remain green.

Use fake time and page transitions for automated tests. A live site remains a
manual regression check because challenge behavior, content, IP reputation, and
availability are external and nondeterministic.

## Files in Scope

- `tools/video_crawler/sniffer.py`
- `tests/test_video_crawler_sniffer_access.py`
- `docs/项目介绍.md` only if its documented behavior needs clarification

No downloader UI change, automatic challenge solver, stealth plugin, CAPTCHA
service, DRM handling, or ordinary-browser profile import is included.

## Acceptance Criteria

- With visible sniffing enabled, an initial verification response no longer
  closes the browser before the configured wait can run.
- Completing the site's permitted manual verification can lead to player
  triggering and media capture in the same browser context.
- Persistent-profile mode retains the verified session through the existing
  context-close behavior.
- Headless restricted pages keep the current fast `HTTP_FORBIDDEN` result.
- A stale initial 403 cannot override a later accessible player state.
- Focused sniffer tests and the full automated test suite pass.
- If the site continues to reject Playwright after the manual wait, the tool
  reports that policy limitation without attempting an automated bypass.
