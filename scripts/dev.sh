#!/bin/sh
# Run both frontend watchers and the reload server, and stop them together.
set -eu

cd "$(dirname "$0")/.."
npm run vendor
sh scripts/watch.sh &
watch_pid=$!
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 &
server_pid=$!

cleanup() {
    trap - EXIT HUP INT TERM
    kill "$server_pid" "$watch_pid" 2>/dev/null || true
    wait "$server_pid" "$watch_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
while kill -0 "$server_pid" 2>/dev/null && kill -0 "$watch_pid" 2>/dev/null; do
    sleep 1
done
if ! kill -0 "$watch_pid" 2>/dev/null; then
    printf '%s\n' 'The asset watchers stopped; stopping the server.' >&2
    exit 1
fi
wait "$server_pid"
