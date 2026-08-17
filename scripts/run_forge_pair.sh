#!/usr/bin/env bash
# Run the flagship forge-pair day with durable logs + a non-Chrome watcher.
#
# Why this exists:
#   - Village stdout is block-buffered when redirected → PYTHONUNBUFFERED=1.
#   - Agent sandboxes often cannot write ~/anima-logs → prefer repo .logs/,
#     and also try ~/anima-logs when writable.
#   - `--monitor` is an HTTP view on the agent's own session. Opening it in
#     Chrome was habit; this script opens Safari when a GUI is available and
#     always starts `python -m anima2.monitor_watch` so a durable watch log
#     exists even with no browser.
#   - Do NOT launch anima-desktop against the same characters — that is a
#     second login and ServUO will kick the agent (docs/MONITORING.md).
#
# Usage:
#   ./scripts/run_forge_pair.sh
#   ./scripts/run_forge_pair.sh --ticks 1800
#   WATCH_PORTS=8801,8802 ./scripts/run_forge_pair.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export PYTHONUNBUFFERED=1

stamp="$(date +%Y%m%d-%H%M)"
mkdir -p "$ROOT/.logs"
LOG_WORK="$ROOT/.logs/forge-${stamp}.log"
LOG_HOME=""
if mkdir -p "$HOME/anima-logs" 2>/dev/null \
  && touch "$HOME/anima-logs/.write-probe" 2>/dev/null; then
  rm -f "$HOME/anima-logs/.write-probe"
  LOG_HOME="$HOME/anima-logs/forge-${stamp}.log"
fi

echo "LOG_WORK=$LOG_WORK"
if [[ -n "$LOG_HOME" ]]; then
  echo "LOG_HOME=$LOG_HOME"
else
  echo "LOG_HOME=(unavailable — using workspace .logs only)"
fi

# Extra village args pass through (default: 1800-tick monitored narrated day).
extra=("$@")
if [[ ${#extra[@]} -eq 0 ]]; then
  extra=(--ticks 1800 --monitor --narrate)
fi

# Start the forge; tee to workspace log, and to ~/anima-logs when possible.
if [[ -n "$LOG_HOME" ]]; then
  uv run python -m anima2.village --forge-pair "${extra[@]}" 2>&1 \
    | tee "$LOG_WORK" "$LOG_HOME" &
else
  uv run python -m anima2.village --forge-pair "${extra[@]}" 2>&1 \
    | tee "$LOG_WORK" &
fi
village_pid=$!

cleanup() {
  if kill -0 "$watch_pid" 2>/dev/null; then
    kill "$watch_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# Wait until monitor ports answer, then open Safari (never Chrome) and start
# the project watcher app.
WATCH_PORTS="${WATCH_PORTS:-8801,8802}"
IFS=',' read -r -a ports <<< "$WATCH_PORTS"
echo "waiting for monitor ports: ${ports[*]}"
for _ in $(seq 1 90); do
  ready=1
  for p in "${ports[@]}"; do
    if ! curl -fsS -m 1 "http://127.0.0.1:${p}/scene.json" >/dev/null 2>&1; then
      ready=0
      break
    fi
  done
  if [[ "$ready" -eq 1 ]]; then
    break
  fi
  sleep 1
done

urls=()
for p in "${ports[@]}"; do
  urls+=("http://127.0.0.1:${p}/")
done

# Prefer Safari as the GUI viewer. Fall back silently if LaunchServices is
# unavailable (agent sandboxes often cannot open GUI apps).
if [[ -d /Applications/Safari.app ]]; then
  open -a /Applications/Safari.app "${urls[@]}" 2>/dev/null \
    || echo "Safari launch skipped (no GUI / sandbox) — use monitor_watch log"
else
  echo "Safari.app not found — use monitor_watch log only"
fi

ANIMA_LOG_DIR="${ANIMA_LOG_DIR:-$ROOT/.logs}" \
  uv run python -m anima2.monitor_watch --ports "$WATCH_PORTS" &
watch_pid=$!
echo "monitor_watch pid=$watch_pid"

wait "$village_pid"
exit_code=$?
echo "EXIT=$exit_code"
echo "LOG_WORK=$LOG_WORK"
[[ -n "$LOG_HOME" ]] && echo "LOG_HOME=$LOG_HOME"
exit "$exit_code"
