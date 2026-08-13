#!/usr/bin/env bash
# Run (or stop) the backend + frontend dev stack.
#
#   ./dev.sh                  backend :8000 + frontend :5173, Ctrl-C stops both
#   DEMO_MODE=true ./dev.sh   same, but with bundled sample data
#   ./dev.sh --stop           stop a stack left running by a previous invocation
#
# Prefer `make dev` / `make demo` / `make stop`, which wrap this.
#
# Note on signals: this script deliberately stays in the terminal's foreground
# process group so Ctrl-C reaches it. `set -m` gives each background job its own
# process group, which lets the cleanup trap kill a server *and its children*
# (uvicorn --reload forks a reloader child that would otherwise survive).

set -euo pipefail
set -m

cd "$(dirname "$0")"
ROOT="$(pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

# Kill any listener on $1 that belongs to this project. Refuses (exit 1) if the
# port is held by an unrelated program rather than killing a stranger's process.
free_port() {
  local port=$1 quiet=${2:-}
  local own_pgid pids pid cmd pgid sig round
  own_pgid=$(ps -p $$ -o pgid= | tr -d ' ')
  for round in 1 2 3 4 5 6 7 8; do
    # Orphaned uvicorn reload-children can shrug off TERM — escalate to KILL.
    sig=TERM
    [ "$round" -ge 3 ] && sig=KILL
    pids=$(lsof -tnP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
    [ -z "$pids" ] && return 0
    for pid in $pids; do
      cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
      # Empty cmd: already dying (e.g. a child of a group we just killed) —
      # let the retry loop observe the port freeing up.
      [ -z "$cmd" ] && continue
      if [[ "$cmd" == *"$ROOT"* ]]; then
        pgid=$(ps -p "$pid" -o pgid= 2>/dev/null | tr -d ' ')
        [ -z "$quiet" ] && echo "dev.sh: stopping dev server on port $port (pid $pid, SIG$sig)"
        if [ -n "$pgid" ] && [ "$pgid" != "$own_pgid" ]; then
          kill "-$sig" -- "-$pgid" 2>/dev/null || true
        fi
        kill "-$sig" "$pid" 2>/dev/null || true
      else
        echo "dev.sh: port $port is used by another program (pid $pid):" >&2
        echo "        $cmd" >&2
        echo "        Stop it or change the port, then re-run." >&2
        exit 1
      fi
    done
    sleep 0.5
  done
  echo "dev.sh: could not free port $port" >&2
  exit 1
}

if [ "${1:-}" = "--stop" ]; then
  free_port "$BACKEND_PORT"
  free_port "$FRONTEND_PORT"
  echo "dev.sh: stack stopped (ports $BACKEND_PORT and $FRONTEND_PORT free)"
  exit 0
fi

free_port "$BACKEND_PORT"
free_port "$FRONTEND_PORT"

BACKEND_PID=""
FRONTEND_PID=""
cleaning=0

cleanup() {
  [ "$cleaning" = 1 ] && return
  cleaning=1
  trap - INT TERM EXIT
  echo
  echo "dev.sh: shutting down…"
  local pid
  # TERM the job's whole process group, then escalate for anything left.
  for pid in "$FRONTEND_PID" "$BACKEND_PID"; do
    [ -n "$pid" ] || continue
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  done
  for _ in 1 2 3 4 5 6; do
    kill -0 "$BACKEND_PID" 2>/dev/null || kill -0 "$FRONTEND_PID" 2>/dev/null || break
    sleep 0.3
  done
  for pid in "$FRONTEND_PID" "$BACKEND_PID"; do
    [ -n "$pid" ] || continue
    kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  done
  # Belt and braces: anything still holding a port is ours to remove.
  free_port "$BACKEND_PORT" quiet || true
  free_port "$FRONTEND_PORT" quiet || true
  echo "dev.sh: stopped"
}
trap cleanup INT TERM EXIT

if [ "${DEMO_MODE:-}" = "true" ]; then
  echo "dev.sh: DEMO MODE — bundled sample data, no console needed"
fi
echo "dev.sh: backend  http://localhost:$BACKEND_PORT"
echo "dev.sh: frontend http://localhost:$FRONTEND_PORT   (Ctrl-C stops both)"

(cd backend && exec uv run uvicorn app.main:app --reload --port "$BACKEND_PORT") &
BACKEND_PID=$!
(cd frontend && exec npm run dev -- --strictPort --port "$FRONTEND_PORT") &
FRONTEND_PID=$!

# Block until either server exits, then the trap takes down the other.
# Deliberately a poll loop, not `wait -n`: macOS ships bash 3.2, which has no
# `wait -n`, and there it fails instantly and tears the stack down on startup.
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done
