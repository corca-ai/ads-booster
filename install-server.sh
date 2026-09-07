#!/usr/bin/env bash
# Linux main-agent installer. Does not modify existing Cloudflare or Mac services.
set -euo pipefail
if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: bash install-server.sh [--check]"
  echo "Install CI-verified main on Linux; requires python3, git, gh, uv and Codex CLI."
  echo "--check only checks prerequisites. Run as the service user, without sudo."
  exit 0
fi
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--check" ) ]]; then
  echo "Unknown option. Use --help." >&2; exit 2
fi
[[ "$(uname -s)" == "Linux" ]] || { echo "This installer requires Linux." >&2; exit 1; }
[[ "$(id -u)" != "0" ]] || { echo "Run as the agent user, without sudo." >&2; exit 1; }
for executable in python3 git gh uv codex systemctl; do
  command -v "$executable" >/dev/null || { echo "Missing prerequisite: $executable" >&2; exit 1; }
done
python3 -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"'
gh api repos/corca-ai/ads-booster/commits/main --jq .sha >/dev/null
if [[ "${1:-}" == "--check" ]]; then echo "Prerequisites ready."; exit 0; fi
umask 077
server_root="$HOME/.local/share/trace-marketing-server"
server_command="$HOME/.local/bin/trace-marketing"
expected_target="$server_root/current/.venv/bin/trace-marketing"
if [[ -e "$server_command" || -L "$server_command" ]]; then
  [[ -L "$server_command" && "$(readlink "$server_command")" == "$expected_target" ]] || {
    echo "Existing trace-marketing command preserved. Resolve the PATH conflict before installing." >&2; exit 1;
  }
fi
if [[ -e "$server_root/current" || -L "$server_root/current" ]]; then
  echo "Existing managed install preserved. Use trace-marketing server update." >&2; exit 1
fi
server_checkout="$(mktemp -d)"
trap 'rm -rf -- "$server_checkout"' EXIT
git clone --quiet --depth 1 --branch main https://github.com/corca-ai/ads-booster.git "$server_checkout/source"
python3 "$server_checkout/source/docs/operations/agent-server/agent-manager.py" bootstrap
mkdir -p "$HOME/.local/bin"
ln -sfn "$expected_target" "$server_command"
echo "Installed verified main. Add ~/.local/bin to PATH if needed."
echo "Next: $server_command server setup"
