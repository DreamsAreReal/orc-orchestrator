# Spike: Ghostty `-e` command execution (Ghostty 1.3.1, macOS)

## Question
Does spawning a worker via Ghostty actually EXECUTE the worker command (and close the
window on exit)? The evaluator (R-M2 round 2, BLOCKER-1) found the F15 spawn path opens an
EMPTY window: the worker never runs.

## Method
Real file-marker probes: each variant runs a command that writes a marker file; we check
whether the file appears. All processes killed between variants. Ghostty version 1.3.1.

## Results (all NOT EXECUTED)
| Variant | Invocation | Marker written? |
|---|---|---|
| A | `open -na Ghostty.app --args -e bash -lc "<multiword string>"` (the shipped F15 form) | NO |
| B | `ghostty -e bash -lc "<string>"` (direct binary) | NO |
| C | `open -na Ghostty.app --args -e bash <script.sh>` | NO |
| D | `open -na Ghostty.app --args -e <script.sh>` (single script path) | NO |
| E | `open -na Ghostty.app --args -e touch <file>` (single simple cmd) | NO |
| F | `open -na Ghostty.app --args -e bash -c "<string>"` | NO |
| G | `open -na Ghostty.app --args -e sh -c "<string>"` | NO |
| H | cold start (all ghostty quit first) + variant A | NO |
| I | variant A with the script as one quoted shell word | NO |
| J | `-e <executable script path>` single token | NO |
| K | direct binary `-e bash -lc` foreground, stderr captured | NO (empty stderr) |
| M1 | `open -na Ghostty.app --args -e top` (Ghostty's own documented example) | NO |
| M3 | `open -a Ghostty.app --args -e ...` (reuse, not -na) | NO |

The process argv confirms the mangling for the `bash -lc` forms: ghostty receives
`ghostty -e bash -lc touch /tmp/.../H.txt; echo H-DONE; sleep 2` where `bash -lc` gets
only `touch` as its `-c` script and the rest become positional params — so even a
single-token script path (variants D/J) opened a window that never ran the command.

macOS note: `ghostty --help` states "launching the terminal emulator from the CLI is not
supported and only actions are supported. Use `open -na Ghostty.app` instead". But
`open -na ... --args -e <cmd>` did not execute the command in this build either. Even the
documented `-e top` example (M1) produced no `top` process.

Also DISPROVES the F15 claim "Ghostty closes the surface on command exit (0 husk)": the
evaluator found husk windows (PIDs 95785 `GHOSTTY_SPIKE`, 9401 `EVALFAST`) whose commands
had finished but the window stayed. Self-close on natural exit is not reliable here.

## Decision
Ghostty `-e` execution is NOT reliable on this machine (1.3.1). By the rule-of-three /
spike rule: DO NOT keep patching Ghostty. Revert the default backend to **Terminal.app**,
which DOES execute the worker command (proven: ran.txt written, window id returned). Solve
the husk-window pain cleanly ON Terminal:
  1. the in-tab command ends with `; exit` so the shell exits cleanly after the worker
     stops (a clean shell exit lets Terminal close the window on profiles that close on
     clean exit);
  2. `close_window` additionally sets the window's tab to close and issues the AppleScript
     close, and (best effort) flips the Terminal "close if the shell exited cleanly"
     behaviour by making the shell exit 0.

Ghostty support stays as an OPT-IN backend (`terminal: ghostty`) but is NOT the default and
is NOT claimed to work until a future Ghostty build / correct invocation is proven by this
same spike. The shipped default must execute workers.
