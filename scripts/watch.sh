#!/bin/sh
# Keep both asset watchers alive even when npm starts them without a TTY.
set -eu

cd "$(dirname "$0")/.."
mkdir -p app/static/css app/static/js
./node_modules/.bin/tailwindcss -i frontend/app.css -o app/static/css/app.css --watch=always &
css_pid=$!
./node_modules/.bin/esbuild frontend/editor.js --bundle --format=iife --target=es2020 \
    --sourcemap --outfile=app/static/js/editor.js --watch=forever &
js_pid=$!

cleanup() {
    trap - EXIT HUP INT TERM
    kill "$css_pid" "$js_pid" 2>/dev/null || true
    wait "$css_pid" "$js_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
while kill -0 "$css_pid" 2>/dev/null && kill -0 "$js_pid" 2>/dev/null; do
    sleep 1
done
printf '%s\n' 'An asset watcher stopped; stopping the other watcher.' >&2
exit 1
