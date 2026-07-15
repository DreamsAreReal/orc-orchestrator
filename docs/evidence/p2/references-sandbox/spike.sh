#!/usr/bin/env bash
# P2-wave spike: prove a sandboxed pipeline worker can READ ~/.claude/skills/** (full
# conveyor: SKILL.md + references/ + templates/ + agents/) but CANNOT WRITE it.
# Root cause fixed: the F1 read-wall blocked every path outside the task workspace, so the
# first live pipeline run logged "references undreadable" and ran on SKILL.md invariants only.
set -euo pipefail
cd "$(dirname "$0")/../../../.."
ROOT="$PWD"
WS="$(mktemp -d -t orc-refs-spike)"
REF="$HOME/.claude/skills/pipeline/references/phase-1-research.md"
SKILL="$HOME/.claude/skills/pipeline/SKILL.md"

PROF="$(python3 -c "import sys;sys.path.insert(0,'$ROOT/src');from orc import sandbox;print(sandbox.write_profile('$WS'))")"
echo "profile: $PROF"

echo "--- READ reference under sandbox (must succeed) ---"
READ_CMD="$(python3 -c "import sys;sys.path.insert(0,'$ROOT/src');from orc import sandbox;print(sandbox.wrap_command('$PROF', 'head -1 \"$REF\"'))")"
if eval "$READ_CMD"; then echo "READ: OK (reference readable under sandbox)"; else echo "READ: FAIL"; exit 1; fi

echo "--- WRITE SKILL.md under sandbox (must be BLOCKED) ---"
WRITE_CMD="$(python3 -c "import sys;sys.path.insert(0,'$ROOT/src');from orc import sandbox;print(sandbox.wrap_command('$PROF', 'echo INJECTED >> \"$SKILL\"'))")"
if eval "$WRITE_CMD" 2>/tmp/orc-refs-w.err; then echo "WRITE: FAIL (write to conveyor was allowed!)"; exit 1; else
  echo "WRITE: BLOCKED as expected -> $(cat /tmp/orc-refs-w.err)"; fi
if grep -q INJECTED "$SKILL"; then echo "INTEGRITY: FAIL (SKILL.md was modified)"; exit 1; fi
echo "INTEGRITY: SKILL.md unchanged"

echo "--- F1 hook: Read reference allowed, /etc blocked, Write-into-skills blocked ---"
python3 - "$ROOT" "$WS" <<'PY'
import sys, os, json, subprocess
root, ws = sys.argv[1], sys.argv[2]
ref = os.path.expanduser("~/.claude/skills/pipeline/references/phase-2-gate.md")
skill = os.path.expanduser("~/.claude/skills/pipeline/SKILL.md")
code = "import sys;sys.path.insert(0,r'%s/src');from orc.worker_walls import hook;hook()" % root
def h(payload):
    p = subprocess.run([sys.executable,"-c",code], input=json.dumps(payload),
                       capture_output=True, text=True, env={**os.environ,"ORC_WORKSPACE":ws})
    return p.returncode
assert h({"tool_name":"Read","tool_input":{"file_path":ref}})==0, "reference read must be allowed"
assert h({"tool_name":"Read","tool_input":{"file_path":"/etc/hosts"}})==2, "other read must block"
assert h({"tool_name":"Write","tool_input":{"file_path":skill}})==2, "write into skills must block"
print("F1 HOOK: read-reference=allow, read-/etc=block, write-skills=block  OK")
PY
echo "P2 REFERENCES-SANDBOX PASS"
