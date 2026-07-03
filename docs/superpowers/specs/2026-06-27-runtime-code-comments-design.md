# Runtime Code Comments Design

## Goal

Add maintainable Chinese documentation to every Python runtime module under
`tools/` without changing application behavior.

## Coverage

- Every `tools/**/*.py` file receives a module docstring.
- Every named class, function, async function, method, and nested helper receives
  a docstring.
- Simple definitions use one concise responsibility sentence.
- Complex definitions document inputs, outputs, raised errors, side effects, and
  design constraints when those details are not evident from the signature.
- Non-obvious algorithms receive nearby inline comments explaining why the
  implementation is structured that way.

## Comment Priorities

Detailed inline explanations focus on:

- PyQt background painting, resize behavior, worker threads, and queue snapshots.
- Playwright access diagnosis, wait policy, response-body extraction, and session
  inheritance.
- M3U8 candidate probing, segment tolerance, bandwidth/resolution ranking, and
  structured timeout handling.
- HLS AES/IV, BYTERANGE, fMP4 maps, discontinuities, live recording, resume state,
  concurrency, cleanup, and FFmpeg merge paths.
- DASH MPD parsing, representation selection, segment expansion, resume state,
  and muxing.

Comments must explain contracts and rationale, not narrate obvious assignments or
repeat the code in Chinese.

## Safety And Verification

- Do not change signatures, conditions, command arguments, constants, or data
  flow while adding comments.
- Add an AST-based test requiring module and definition docstrings across all
  runtime Python files.
- Run `compileall`, focused tests after each batch, the AST audit, and the full
  unittest suite at completion.
