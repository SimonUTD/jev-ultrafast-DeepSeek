#!/bin/zsh
# Dedicated automation Chrome for the demo server and live checks (macOS).
#
# Why a dedicated instance: macOS privacy blocks reading the default profile's
# DevToolsActivePort (the harness daemon dies with EPERM), and Chrome 147+ locks
# CDP on the default profile. A separate --user-data-dir on port 9333 avoids both.
# Optional proxy port routes the Google Flights scenario through a local proxy
# (e.g. Clash on 7890) for networks without direct Google access.
#
# Usage: scripts/dev_chrome.sh [proxy-port]     then:
#   BU_CDP_URL=http://127.0.0.1:9333 uv run jev
set -u
cd "$(dirname "$0")/.."
PORT=9333
PROFILE="$PWD/artifacts/jev-chrome-profile"
PROXY_ARG=""
[ -n "${1:-}" ] && PROXY_ARG="--proxy-server=http://127.0.0.1:$1"

if curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" > /dev/null; then
  echo "dev chrome already listening on $PORT"
else
  pgrep -f "remote-debugging-port=$PORT" > /dev/null 2>&1 && kill $(pgrep -f "remote-debugging-port=$PORT") && sleep 1
  mkdir -p "$PROFILE"
  nohup "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --headless=new --remote-debugging-port=$PORT $PROXY_ARG \
    --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check --disable-gpu \
    about:blank > /tmp/jev-dev-chrome.log 2>&1 &
  for _ in {1..20}; do
    curl -s --max-time 1 "http://127.0.0.1:$PORT/json/version" > /dev/null && break
    sleep 0.5
  done
fi

# Persistent consent cookie so google.com does not redirect the Flights scenario
# to consent.google.com (whose controls the DOM reader cannot see). Needs Google
# reachable (pass the proxy port on such networks); skips quietly otherwise.
BU_CDP_URL="http://127.0.0.1:$PORT" uv run python - > /dev/null 2>&1 <<'PY' \
  && echo "consent cookie set" || echo "cookie skipped: google unreachable (pass proxy port)"
from jev_ultrafast.browser import Browser
b = Browser("https://www.google.com/travel/flights?hl=en")
b.call("Network.enable")
assert b.call("Network.setCookie", name="SOCS",
              value="CAISHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmVuIAEaBgiA_LiXBg",
              domain=".google.com", path="/", secure=True, expires=4102444800)["success"]
b.close()
PY

echo "ready — start the demo with:"
echo "  BU_CDP_URL=http://127.0.0.1:$PORT uv run jev"
