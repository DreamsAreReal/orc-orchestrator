# Spike: OS-sandbox (macOS seatbelt) as the PRIMARY wall over the pattern-hook (F13)

## Why (finding from eval M1)

The F1 deny-wall is a PreToolUse pattern-matcher on the Bash command string. It is
fundamentally bypassable by obfuscation because the dangerous action never appears
literally in the matched command:

- `echo <base64-of-rm> | base64 -d | bash`  (the `rm` is hidden in base64)
- `python3 -c "import shutil; shutil.rmtree(...)"` (no `rm` token at all)
- `find /outside/ws -delete` (deletion via `find`, not `rm`)
- `xargs rm` (rm arrives via a pipe, not the literal command)

A pattern-matcher cannot enumerate every encoding. For an UNATTENDED bypass-permissions
worker we need an **OS-level** boundary that does not care how the syscall was reached.

## Mechanism chosen: `sandbox-exec` + a seatbelt profile

macOS ships `/usr/bin/sandbox-exec` (seatbelt). A profile denies `file-write*` everywhere
and re-allows it ONLY inside the task workspace. Because the kernel enforces the write at
the syscall level, it does not matter whether the write came from `rm`, `find -delete`,
`python`, or a base64-decoded shell — all are blocked. This covers child processes too
(the profile is inherited).

We prove this WITHOUT a full claude worker (the ccusage window is precious): a
`sandbox-exec` wrapper + a probe action is exactly the same enforcement path a sandboxed
worker's Bash tool would take.

## Profile (the shape orc generates per worker)

```
(version 1)
(allow default)
(deny file-write*)
(allow file-write*
  (subpath "<WORKSPACE>"))        ; ONLY the task workspace is writable
(allow file-write-data
  (literal "/dev/null") (literal "/dev/stdout")
  (literal "/dev/stderr") (literal "/dev/tty"))
```

**Critical profile-design finding (a real trap I hit):** do NOT whitelist a broad parent
directory. My first profile allowed `/private/tmp`, and because a sentinel happened to
live under `/private/tmp`, the sandbox correctly ALLOWED the delete — a false "leak" that
was actually my over-broad allowlist. The workspace allow-rule must be the *narrow*
`subpath` of the task workspace only. Verified: with the sentinel in `$HOME` (outside any
allowed subpath) every escape is blocked.

## Proof (sentinel in `$HOME`, workspace elsewhere; all run under `sandbox-exec`)

| # | Obfuscated escape (outside workspace)                         | Result |
|---|---------------------------------------------------------------|--------|
| A | `rm -f <sentinel>`                                            | `Operation not permitted` — **sentinel SURVIVED** |
| B | `echo <base64> \| base64 -d \| bash`  (decodes to the rm)      | `Operation not permitted` — **sentinel SURVIVED** |
| C | `python3 -c "shutil.rmtree(<outdir>)"`                        | no-op — **sentinel SURVIVED** |
| D | `find <outdir> -name sentinel -delete`                        | `unlink: Operation not permitted` — **sentinel SURVIVED** |
| E | write to `~/.ssh/…`                                           | `Operation not permitted` — **BLOCKED** |
| ✓ | control: `echo ok > <workspace>/in.txt`                       | wrote-inside — **works** |

Raw session output is captured by `.verify/sandbox-walls.sh` into `docs/evidence/F13/`.

## Network

- `(deny network*)` fully blocks outbound (verified: `curl https://example.com` → BLOCKED).
- A precise per-remote-host allowlist is NOT reliable in user seatbelt (the coarse
  `(allow network-outbound (remote ...))` forms still let `curl` through in testing). So the
  honest policy is binary: network fully allowed (default — workers need the claude API,
  git fetch, brew) OR fully denied (`sandbox_deny_network` for locked-down runs). Per-host
  MCP/egress allowlisting stays at the application layer, not the kernel profile.
- `git push` remains blocked by the F1 pattern-hook (secondary layer) even with network on;
  the FS sandbox is the PRIMARY wall for the destructive-write class F1 could not cover.

## Integration decision

- orc generates one profile per spawn (workspace subpath baked in) and wraps the worker
  command: `sandbox-exec -f <profile> bash -lc '<start command>'`. The PreToolUse hook (F1)
  stays as the secondary layer (defense in depth; catches `git push`, gives the model a
  readable block reason). The sandbox is the boundary that survives obfuscation.
- `claude` itself also ships `/sandbox` (Sandbox mode) and the SDK `sandbox` setting; those
  are the same seatbelt mechanism owned by claude. orc uses `sandbox-exec` at the SPAWN
  layer so the wall holds regardless of the worker's own settings (the worker cannot
  weaken a boundary imposed by its parent process).
