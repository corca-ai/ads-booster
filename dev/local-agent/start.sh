#!/bin/sh
set -eu
umask 077
if [ ! -f /state/service-token ]; then
  python -c 'import secrets; from pathlib import Path; Path("/state/service-token").write_text(secrets.token_urlsafe(32))'
fi
export TRACE_MARKETING_SERVICE_TOKEN="$(cat /state/service-token)"
if [ ! -f "$TRACE_MARKETING_KNOWLEDGE_POLICY" ]; then
  trace-marketing knowledge init --root "$TRACE_MARKETING_KNOWLEDGE_ROOT" \
    --control-root "$TRACE_MARKETING_KNOWLEDGE_CONTROL_ROOT" \
    --policy "$TRACE_MARKETING_KNOWLEDGE_POLICY" --workspace trace
fi
socat TCP-LISTEN:8090,fork,reuseaddr TCP:127.0.0.1:8091 &
exec trace-marketing service run --model gpt-6-astra --home /state/agent \
  --host 127.0.0.1 --port 8091 --tenant trace
