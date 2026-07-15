#!/usr/bin/env bash
# Build two smoke fixtures for F11 characterization:
#   A) standard workspace  W_A/docs/STATE.md
#   B) tasks-layout        W_B/docs/tasks/<slug>/docs/STATE.md  (nested sub-workspace)
# STATE.md content is minimal but has the sections scorecard/lint-state check.
set -eu
FIX="$1"
rm -rf "$FIX"; mkdir -p "$FIX"

make_state() {
  # $1 = docs dir
  local D="$1"
  mkdir -p "$D"
  cat > "$D/STATE.md" <<'EOF'
# STATE — smoke fixture

## Мета
Задача: smoke fixture task for characterization.

## Рекап задачи
Fixture used to characterize pipeline scorecard/hooks behavior.

## Следующий шаг
Nothing — this is a fixture.
EOF
}

# --- A) standard ---
WA="$FIX/proj-a"
make_state "$WA/docs"
git -C "$WA" init -q 2>/dev/null || true
git -C "$WA" add -A 2>/dev/null || true
git -C "$WA" -c user.email=f@x -c user.name=f commit -qm init 2>/dev/null || true

# --- B) tasks layout: <proj>/docs/tasks/<slug>/STATE.md (design.md contract) ---
WB="$FIX/proj-b"
mkdir -p "$WB/docs"                       # product layer exists but has no STATE.md
make_state "$WB/docs/tasks/add-widget"    # task mini-pipe: STATE.md directly here
git -C "$WB" init -q 2>/dev/null || true
git -C "$WB" add -A 2>/dev/null || true
git -C "$WB" -c user.email=f@x -c user.name=f commit -qm init 2>/dev/null || true

echo "A=$WA"
echo "B=$WB"
