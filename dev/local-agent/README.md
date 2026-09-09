# Local Docker agent

Run from the repository root:

```sh
docker compose -f dev/local-agent/compose.yaml up -d --build
curl --fail http://127.0.0.1:18090/health
docker compose -f dev/local-agent/compose.yaml exec agent codex login status
```

The image installs the current source as a non-editable Python package and pins Codex CLI to
0.153.4. Python is 3.14; the reasoning model is gpt-6-astra. Rebuild after source changes.
No host Slack, Notion or GitHub configuration is inherited. The API is exposed only at
127.0.0.1:18090; inside Docker, socat forwards to the service's authenticated loopback listener.

Persistent state is in the ignored `.agent-workspace/docker-test/` directory:

- `agent/marketing-agent/service/`: canonical service SQLite and artifacts.
- `knowledge/`, `knowledge-control/`, `knowledge-policy.json`: initialized knowledge runtime.
- `codex/`: isolated Codex login and runtime state.
- `service-token`: generated API token, never committed or printed by startup.

For a new login, run `docker compose -f dev/local-agent/compose.yaml exec agent codex login --device-auth`
and complete the interactive instructions. Compose does not copy host credentials automatically. For this workstation setup, only the existing
local Codex auth file was copied once into isolated state and login was verified; host Codex
configuration and skills are not mounted. A fresh state directory requires login again.

An authenticated capability check without printing the token:

```sh
docker compose -f dev/local-agent/compose.yaml exec -T agent python - <<'PY'
import json
import urllib.request
from pathlib import Path
request = urllib.request.Request(
    'http://127.0.0.1:8091/v1/tools',
    headers={'Authorization': 'Bearer ' + Path('/state/service-token').read_text().strip()},
)
with urllib.request.urlopen(request) as response:
    print(json.dumps(json.load(response), indent=2))
PY
```

Stop with `docker compose -f dev/local-agent/compose.yaml down`. State survives container
recreation. Do not delete the state directory unless intentionally discarding test data and login.
Service readiness does not establish memory learning, cross-thread recall or production Slack
behavior; those require subsequent conversation scenarios and receipt inspection.

## Real-model memory rehearsal

The opt-in `tests/marketing/agent_service/slack_memory_canary.py` sends signed synthetic Slack
mentions through the installed Slack adapter with the installed knowledge runtime. All Slack
responses are captured locally. It creates no approved memory directly. Each invocation is a new
Python process; only the state directory survives. Run from the repository root:

```sh
docker cp tests/marketing/agent_service/slack_memory_canary.py trace-local-test-agent-1:/state/slack_memory_canary.py
# Choose a NEW directory for each complete rehearsal.
TEST_ROOT=/tmp/memory-rehearsal-new
docker compose -f dev/local-agent/compose.yaml exec -T agent trace-marketing knowledge init \
  --root "$TEST_ROOT/knowledge" --control-root "$TEST_ROOT/control" \
  --policy "$TEST_ROOT/policy.json" --workspace memory-rehearsal
for turn in 0 1 2 3 4 5; do
  docker compose -f dev/local-agent/compose.yaml exec -T agent python -I /state/slack_memory_canary.py \
    --root "$TEST_ROOT" --turn "$turn" --recall-only || break
done
docker compose -f dev/local-agent/compose.yaml restart
docker compose -f dev/local-agent/compose.yaml exec -T agent python -I /state/slack_memory_canary.py \
  --root "$TEST_ROOT" --turn 6 --recall-only
docker compose -f dev/local-agent/compose.yaml exec -T agent python -I /state/slack_memory_canary.py \
  --root "$TEST_ROOT" --turn 7 --recall-only
docker cp "trace-local-test-agent-1:$TEST_ROOT" .agent-workspace/docker-test/
```

Turns cover setup, correction, an unrelated question, same-thread recall, new-thread recall,
explicit memory request, post-restart new-thread recall, and an unrelated project's unknown budget.
Turn 4 checks automatic memory before the explicit memory request in turn 5. `--recall-only` avoids requesting
content production in a fresh store without brand voice configuration. Omit it to inspect that
separate preparation boundary. The rehearsal explicitly drains curation after each turn instead
of waiting for the normal collection interval; it does not validate scheduler timing.

Add `--natural-drain` to each canary invocation to tick the normal worker through its real collection
deadline instead of forcing a flush. This records batch/job transitions and fails if the bounded
300-second observation window expires. After draining, check search readiness separately from the
earlier response receipt, which can legitimately report indexing still pending at response time.

The rehearsal uses native container storage; inspect live SQLite through `docker compose exec`,
and export the directory after completion as shown above. `/tmp` survives restart but is discarded
on container recreation. Inspect `turn-N.json`, `service/agent-service.sqlite3` and
`knowledge/index.sqlite` in the exported directory. A captured `curation_error` is a failed pipeline stage, not a passing
result. Run states alone do not prove answer correctness; inspect dialogue and provider/context
receipts. Production Slack delivery and server-release parity remain separate checks.
