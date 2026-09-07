#!/usr/bin/env bash
# Ubuntu bootstrap. Installs missing tools only; never changes existing tunnel services.
set -euo pipefail
check=0
source_dir=""
service_user=""
seen=" "
while [[ $# -gt 0 ]]; do
  [[ "$seen" != *" $1 "* ]] || { echo "Duplicate option: $1" >&2; exit 2; }
  seen+="$1 "
  case "$1" in
    --help) cat <<'HELP'
Usage: bash install-server.sh [--check] [--user USER] [--source DIRECTORY]
Ubuntu 22.04/24.04, x86_64/aarch64; installs missing prerequisites automatically.
Default: install CI-verified public main. No GitHub account required.
--check             Read-only local prerequisite report; does not install or contact GitHub.
--user USER         Required when root; create/use this unprivileged service account.
--source DIRECTORY  Developer install from a local checkout (not a CI-verified release).
                    Automatic updates still follow verified public main after setup/start.
Rerun to resume installation. Existing configured installs and tools are preserved.
HELP
      exit 0 ;;
    --check) check=1; shift ;;
    --user|--source)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || { echo "Missing option value" >&2; exit 2; }
      if [[ "$1" == --user ]]; then service_user="$2"; else source_dir="$2"; fi
      shift 2 ;;
    *) echo "Unknown option: $1. Use --help." >&2; exit 2 ;;
  esac
done
[[ "$(uname -s)" == Linux ]] || { echo "Ubuntu Linux is required." >&2; exit 1; }
# shellcheck source=/dev/null
. /etc/os-release
[[ "$ID" == ubuntu && ( "$VERSION_ID" == 22.04 || "$VERSION_ID" == 24.04 ) ]] || {
  echo "Supported: Ubuntu 22.04/24.04." >&2; exit 1;
}
arch="$(uname -m)"
[[ "$arch" == x86_64 || "$arch" == aarch64 ]] || { echo "Unsupported CPU: $arch" >&2; exit 1; }
export PATH="$HOME/.local/bin:$PATH"
if [[ "$check" == 1 ]]; then
  missing=0
  for tool in python3 git curl tar sha256sum uv codex cloudflared systemctl; do
    if command -v "$tool" >/dev/null; then echo "$tool: present"; else echo "$tool: missing (installer will prepare)"; missing=1; fi
  done
  exit "$missing"
fi
if [[ "$(id -u)" == 0 ]]; then
  [[ "$service_user" =~ ^[a-z_][a-z0-9_-]*$ && "$service_user" != root ]] || {
    echo "Use: bash install-server.sh --user trace-marketing (agent never runs as root)." >&2; exit 2;
  }
else
  [[ -z "$service_user" ]] || { echo "--user requires root." >&2; exit 2; }
fi
if [[ -n "$source_dir" ]]; then
  source_dir="$(cd "$source_dir" && pwd)"
  [[ -f "$source_dir/docs/operations/agent-server/agent-manager.py" ]] || { echo "Invalid source checkout" >&2; exit 2; }
fi
sudo_cmd=()
if [[ "$(id -u)" != 0 ]]; then
  sudo_cmd=(sudo)
fi
echo "[1/4] Prepare Ubuntu dependencies and login-independent user services."
packages=(python3 git curl ca-certificates tar dbus-user-session)
need_packages=0
for package in "${packages[@]}"; do
  [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" == 'install ok installed' ]] || need_packages=1
done
if [[ "$need_packages" == 1 ]]; then
  "${sudo_cmd[@]}" apt-get update -qq
  "${sudo_cmd[@]}" apt-get install -y -qq "${packages[@]}"
fi
if [[ "$(id -u)" == 0 ]]; then
  if ! id "$service_user" >/dev/null 2>&1; then useradd --create-home --shell /bin/bash "$service_user"; fi
  [[ "$(id -u "$service_user")" != 0 ]] || { echo "Refusing root account" >&2; exit 2; }
  loginctl enable-linger "$service_user"
  # runuser supplies the real account's HOME; do not reuse root's credentials or config.
  script_copy="$(mktemp /tmp/trace-installer.XXXXXX)"
  cp -- "$0" "$script_copy"
  chmod 755 "$script_copy"
  trap 'rm -f -- "$script_copy"' EXIT
  args=()
  [[ -z "$source_dir" ]] || args=(--source "$source_dir")
  runuser -u "$service_user" -- bash "$script_copy" "${args[@]}"
  echo "Continue as this user: sudo -iu $service_user"
  exit 0
fi
if [[ "$(loginctl show-user "$(id -u)" --property=Linger --value 2>/dev/null || true)" != yes ]]; then
  "${sudo_cmd[@]}" loginctl enable-linger "$(id -un)"
fi
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
umask 077
server_root="$HOME/.local/share/trace-marketing-server"
server_command="$HOME/.local/bin/trace-marketing"
expected_target="$server_root/current/.venv/bin/trace-marketing"
if [[ -e "$server_command" || -L "$server_command" ]]; then
  [[ -L "$server_command" && "$(readlink "$server_command")" == "$expected_target" ]] || {
    echo "Existing trace-marketing command preserved. Resolve the PATH conflict before installing." >&2; exit 1;
  }
fi
server_checkout="$(mktemp -d)"
trap 'rm -rf -- "$server_checkout"' EXIT
mkdir -p "$HOME/.local/bin"
fetch() {
  curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 "$1" -o "$server_checkout/asset"
  echo "$2  $server_checkout/asset" | sha256sum --check --status || { echo "Download checksum mismatch" >&2; exit 1; }
}
echo "[2/4] Prepare pinned uv, Codex and cloudflared (existing commands preserved)."
if ! command -v uv >/dev/null; then
  if [[ "$arch" == x86_64 ]]; then digest=8681d8921e7d520fb368991dcf5f9c1905b80f5bf2a265a0ed085c8d8e342477;
  else digest=d58030acd26159499ac82f32da12d1b3c12a3a1bfc414232d9082070c03e128d; fi
  fetch "https://github.com/astral-sh/uv/releases/download/0.12.6/uv-$arch-unknown-linux-gnu.tar.gz" "$digest"
  tar -xzf "$server_checkout/asset" -C "$server_checkout"
  install -m 755 "$server_checkout/uv-$arch-unknown-linux-gnu/uv" "$HOME/.local/bin/uv"
fi
if ! command -v codex >/dev/null; then
  if [[ "$arch" == x86_64 ]]; then digest=f479424eca092484dc40d87ae28c44f4cc40234a60045d6131e493800d814a30;
  else digest=5cda6182bd94c3a30f2eb63a495489ebf7f691fddb14d70f48c6c1a5071b6cde; fi
  fetch "https://github.com/openai/codex/releases/download/rust-v0.153.4/codex-$arch-unknown-linux-musl.tar.gz" "$digest"
  tar -xzf "$server_checkout/asset" -C "$server_checkout"
  install -m 755 "$server_checkout/codex-$arch-unknown-linux-musl" "$HOME/.local/bin/codex"
fi
if ! command -v cloudflared >/dev/null; then
  if [[ "$arch" == x86_64 ]]; then cf_arch=amd64; digest=f29324fe934d1e100617484c78deef803c4dc2cd351d645bbde42e96b4fccc5e;
  else cf_arch=arm64; digest=4bcfd35521a7cbc545ebfd5d57334a71ee180e2a64874981f374c81472118391; fi
  fetch "https://github.com/cloudflare/cloudflared/releases/download/2026.8.3/cloudflared-linux-$cf_arch" "$digest"
  install -m 755 "$server_checkout/asset" "$HOME/.local/bin/cloudflared"
fi
echo "[3/4] Install isolated agent and locked Python dependencies."
if [[ -e "$server_root/current" || -L "$server_root/current" ]]; then
  [[ -x "$expected_target" && -f "$server_root/current/agent-manager.py" ]] || { echo "Managed install damaged; preserve files and inspect current symlink." >&2; exit 1; }
  echo "Existing managed installation preserved."
elif [[ -n "$source_dir" ]]; then
  python3 "$source_dir/docs/operations/agent-server/agent-manager.py" source --source "$source_dir"
else
  git clone --quiet --depth 1 --branch main https://github.com/corca-ai/ads-booster.git "$server_checkout/source"
  python3 "$server_checkout/source/docs/operations/agent-server/agent-manager.py" bootstrap
fi
ln -sfn "$expected_target" "$server_command"
echo "[4/4] Command ready: $server_command"
echo 'For this shell: export PATH="$HOME/.local/bin:$PATH"'
echo "Next (as $(id -un)): codex login --device-auth"
echo "Then: $server_command server setup"
echo "Then: $server_command server doctor && $server_command server start"
