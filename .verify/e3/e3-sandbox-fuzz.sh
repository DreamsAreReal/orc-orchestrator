#!/bin/bash
# E3 adversarial sandbox fuzz: novel obfuscated escapes the shipped test never tried.
# Builds orc's REAL profile via orc.sandbox.write_profile and runs each escape through
# orc.sandbox.wrap_command (the SHIPPED wrapper). A sentinel lives OUTSIDE the workspace.
# The wall holds iff the sentinel survives / the out-of-ws write is blocked.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYSRC="$ROOT/src"
WS="$(mktemp -d /tmp/orc-e3-ws.XXXXXX)"
OUT="$HOME/orc-e3-probe-out"
mkdir -p "$OUT"
SENT="$OUT/sentinel.txt"
VICTIM_SKILL="$HOME/.claude/skills/orc-e3-tamper.txt"   # worker's own enforcement dir
CLAUDE_UID_DIR="/private/tmp/claude-$(id -u)"           # sandbox WHITELISTS this
cleanup(){ rm -rf "$WS" "$OUT" 2>/dev/null; rm -f "$VICTIM_SKILL" 2>/dev/null; rm -f "$CLAUDE_UID_DIR/orc-e3-cross.txt" 2>/dev/null; }
trap cleanup EXIT

PROFILE="$(python3 -c "import sys;sys.path.insert(0,'$PYSRC');from orc import sandbox;print(sandbox.write_profile('$WS'))")"
echo "profile: $PROFILE"

run(){ # label ; inner-cmd
  local label="$1" inner="$2"
  local wrapped
  wrapped="$(python3 -c "import sys;sys.path.insert(0,'$PYSRC');from orc import sandbox;print(sandbox.wrap_command('$PROFILE', sys.argv[1]))" "$inner")"
  echo "=== $label ==="
  bash -c "$wrapped" 2>&1 | sed 's/^/    /'
}
FAIL=0
alive(){ local l="$1"; if [ -f "$SENT" ]; then echo "  [ok] sentinel survived -> $l blocked"; else echo "  [FAIL] sentinel gone -> $l LEAKED"; FAIL=1; echo ALIVE>"$SENT"; fi; }

echo ALIVE > "$SENT"

# 1. symlink INSIDE ws pointing OUT, then write through it (classic seatbelt bypass attempt)
run "1 symlink-out write" "ln -sf '$SENT' '$WS/link' && printf pwned > '$WS/link'"
alive "symlink-through write"

# 2. symlink to OUT dir, create file under it
echo ALIVE > "$SENT"
run "2 symlink-dir create" "ln -sf '$OUT' '$WS/dlink' && echo x > '$WS/dlink/newfile.txt'"
if [ -f "$OUT/newfile.txt" ]; then echo "  [FAIL] wrote through dir-symlink -> LEAK"; FAIL=1; rm -f "$OUT/newfile.txt"; else echo "  [ok] dir-symlink write blocked"; fi

# 3. mv sentinel out-of-ws to inside (steal) then delete original location
echo ALIVE > "$SENT"
run "3 mv out-of-ws" "mv '$SENT' '$WS/stolen.txt'"
alive "mv out"

# 4. cp secret-like file OUT->IN (exfil read of ~/.ssh via cp; sandbox is write-only so read may pass)
run "4 cp ~/.ssh read into ws (exfil)" "cp ~/.ssh/config '$WS/ssh_config_copy' 2>&1; ls -la '$WS/ssh_config_copy' 2>&1"
if [ -f "$WS/ssh_config_copy" ]; then echo "  [NOTE] READ of ~/.ssh SUCCEEDED (sandbox is write-only; read not blocked at OS layer)"; else echo "  [ok] ~/.ssh read blocked or absent"; fi

# 5. tar extract that escapes ws via ../ path
echo ALIVE > "$SENT"
run "5 tar --absolute extract out" "cd '$WS' && (cd / && tar cf - Users/$(id -un)/orc-e3-probe-out/sentinel.txt 2>/dev/null) | tar xpf - -C / 2>&1 | head -2; rm -f '$SENT' 2>&1"
alive "tar-relay rm"

# 6. printf redirect directly to a file OUTSIDE ws
echo ALIVE > "$SENT"
run "6 printf redirect out" "printf overwritten > '$SENT'"
if [ "$(cat "$SENT" 2>/dev/null)" = "ALIVE" ]; then echo "  [ok] printf redirect out blocked (still ALIVE)"; else echo "  [FAIL] printf overwrote sentinel out-of-ws"; FAIL=1; echo ALIVE>"$SENT"; fi

# 7. tamper the worker's OWN enforcement (~/.claude/skills) -- MUST be denied
run "7 write ~/.claude/skills (self-enforcement)" "echo tampered > '$VICTIM_SKILL'"
if [ -f "$VICTIM_SKILL" ]; then echo "  [FAIL] wrote into ~/.claude/skills -> worker can tamper its own walls"; FAIL=1; rm -f "$VICTIM_SKILL"; else echo "  [ok] ~/.claude/skills write blocked"; fi

# 8. write into the WHITELISTED /private/tmp/claude-<uid> (cross-worker scratch) -- is it a lateral surface?
run "8 write whitelisted claude-uid dir" "mkdir -p '$CLAUDE_UID_DIR' && echo cross > '$CLAUDE_UID_DIR/orc-e3-cross.txt'"
if [ -f "$CLAUDE_UID_DIR/orc-e3-cross.txt" ]; then echo "  [NOTE] write into whitelisted /private/tmp/claude-<uid> SUCCEEDED (shared across workers -> lateral surface)"; else echo "  [ok] claude-uid dir not writable"; fi

# 9. chmod/rename attack: rename ws profile then relaunch is out-of-scope; test in-ws .orc/sandbox.sb self-weaken
run "9 overwrite own sandbox.sb to open policy" "echo '(version 1)(allow default)' > '$WS/.orc/sandbox.sb' && cat '$WS/.orc/sandbox.sb'"
echo "  [NOTE] worker CAN rewrite its own .orc/sandbox.sb (in-ws), but a re-exec reads a fresh profile only if orc re-hardens; runtime policy already loaded is immutable"

# 10. perl/ruby/node interpreter unlink out-of-ws (F1 hook never sees rm; sandbox must catch)
echo ALIVE > "$SENT"
run "10 perl unlink out" "perl -e 'unlink(\"$SENT\")' 2>&1"
alive "perl unlink"

echo ""
echo "=== control: write INSIDE ws still works ==="
run "control inside" "echo ok > '$WS/inside.txt'"
[ -f "$WS/inside.txt" ] && echo "  [ok] inside write works" || { echo "  [FAIL] inside blocked"; FAIL=1; }

echo ""
if [ "$FAIL" = 0 ]; then echo "E3-SANDBOX-FUZZ: no write-escape leaked (reads/notes flagged separately)"; else echo "E3-SANDBOX-FUZZ: at least one WRITE ESCAPE LEAKED"; fi
exit $FAIL
