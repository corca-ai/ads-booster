#!/usr/bin/env bash

set -Eeuo pipefail

readonly REPOSITORY="corca-ai/ads-booster"
readonly DEFAULT_HOME="${HOME:?HOME is required}/.trace-agent"
readonly DEFAULT_INSTALL_ROOT="$HOME/.local/share/trace-marketing"

tag="${TRACE_ADS_TAG:-}"
agent_home="${TRACE_AGENT_HOME:-$DEFAULT_HOME}"
install_root="${TRACE_MARKETING_INSTALL_ROOT:-$DEFAULT_INSTALL_ROOT}"
uv_path="${TRACE_ADS_UV:-}"
interval_seconds="${TRACE_MARKETING_UPDATE_INTERVAL_SECONDS:-3600}"
dry_run=0
download_directory=""

die() {
    printf 'trace-marketing installer: %s\n' "$*" >&2
    exit 1
}

print_help() {
    cat <<'EOF'
Bootstrap the Trace Mac worker from a stable, provenance-verified GitHub Release. The command
installs trace-marketing into versioned directories, preserves worker/Codex state, and creates
separate worker and updater LaunchAgents. It never upgrades Codex CLI, Xcode, Appium, XCUITest,
or the Trace app.

Usage:
  install.sh [options]

Options:
  --tag <vX.Y.Z>          Exact stable release; defaults to GitHub's latest stable release.
  --home <path>           Existing worker state root (default: ~/.trace-agent).
  --install-root <path>   Versioned product root (default: ~/.local/share/trace-marketing).
  --uv <path>             Existing uv executable; uv is never installed or upgraded here.
  --interval-seconds <n>  Updater poll interval, at least 300 (default: 3600).
  --dry-run               Print the verified-release bootstrap plan only.
  -h, --help              Show this help.

Prerequisites:
  Apple Silicon macOS, authenticated GitHub CLI with artifact-attestation support, uv with local
  Python 3.14, authenticated official Codex CLI, Appium/XCUITest, Xcode Simulator, and
  com.corca.Trace. A fresh Mac may install before enrollment;
  the worker credential is required only when starting the services.
  An operator must drain and stop any existing worker LaunchAgent before this one-time bootstrap.
EOF
}

require_value() {
    (($# >= 2)) || die "$1 requires a value"
}

while (($# > 0)); do
    case "$1" in
        --tag)
            require_value "$@"
            tag="$2"
            shift 2
            ;;
        --tag=*)
            tag="${1#*=}"
            shift
            ;;
        --home)
            require_value "$@"
            agent_home="$2"
            shift 2
            ;;
        --home=*)
            agent_home="${1#*=}"
            shift
            ;;
        --install-root)
            require_value "$@"
            install_root="$2"
            shift 2
            ;;
        --install-root=*)
            install_root="${1#*=}"
            shift
            ;;
        --uv)
            require_value "$@"
            uv_path="$2"
            shift 2
            ;;
        --uv=*)
            uv_path="${1#*=}"
            shift
            ;;
        --interval-seconds)
            require_value "$@"
            interval_seconds="$2"
            shift 2
            ;;
        --interval-seconds=*)
            interval_seconds="${1#*=}"
            shift
            ;;
        --dry-run)
            dry_run=1
            shift
            ;;
        --source|--source=*|--from|--from=*|--ref|--ref=*|--bin-dir|--bin-dir=*|--no-shell-update)
            die "$1 is unsafe for production; select a versioned release with --tag"
            ;;
        -h|--help)
            print_help
            exit 0
            ;;
        *)
            die "unknown option: $1 (use --help)"
            ;;
    esac
done

[[ -z "$tag" || "$tag" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || \
    die "--tag must use vX.Y.Z strict semantic versioning"
[[ "$agent_home" == /* ]] || die "--home must be an absolute path"
[[ "$install_root" == /* ]] || die "--install-root must be an absolute path"
[[ "$interval_seconds" =~ ^[0-9]+$ ]] || die "--interval-seconds must be an integer"
((interval_seconds >= 300)) || die "--interval-seconds must be at least 300"

if [[ "$dry_run" == "1" ]]; then
    printf 'trace-marketing verified release bootstrap (dry run)\n'
    printf '  repository: %s\n' "$REPOSITORY"
    printf '  release: %s\n' "${tag:-latest stable}"
    printf '  agent state preserved: %s\n' "$agent_home"
    printf '  managed releases: %s/releases/<version>\n' "$install_root"
    printf '  verification: stable + tag/commit + SHA-256 + workflow-bound attestations\n'
    printf '  services: separate worker and pull updater LaunchAgents\n'
    printf '  out of scope: Codex CLI, Xcode, Appium, XCUITest, Trace app upgrades\n'
    exit 0
fi

[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || \
    die "bootstrap requires an Apple Silicon Mac"
command -v gh >/dev/null 2>&1 || die "GitHub CLI is required"
gh attestation verify --help >/dev/null 2>&1 || \
    die "GitHub CLI must support artifact attestation verification; upgrade gh manually"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
if [[ -z "$uv_path" ]]; then
    uv_path="$(command -v uv || true)"
fi
[[ -n "$uv_path" && -x "$uv_path" ]] || die "uv must already be installed; pass --uv if needed"

if [[ -z "$tag" ]]; then
    tag="$(gh release view --repo "$REPOSITORY" --json tagName,isDraft,isPrerelease \
        --jq 'select(.isDraft == false and .isPrerelease == false) | .tagName')"
    [[ -n "$tag" ]] || die "latest stable GitHub Release could not be resolved"
fi

download_directory="$(mktemp -d "${TMPDIR:-/tmp}/trace-marketing-bootstrap.XXXXXX")"
cleanup() {
    if [[ -n "$download_directory" && -d "$download_directory" ]]; then
        rm -rf -- "$download_directory"
    fi
}
trap cleanup EXIT

gh release download "$tag" --repo "$REPOSITORY" --dir "$download_directory" \
    --pattern trace-marketing-release.json \
    --pattern trace-marketing-bootstrap.py
manifest="$download_directory/trace-marketing-release.json"
bootstrap="$download_directory/trace-marketing-bootstrap.py"
[[ -f "$manifest" && -f "$bootstrap" ]] || die "release bootstrap envelope is incomplete"

bundle_name="$(python3 -c '
import json, pathlib, sys
payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
if payload.get("tag") != sys.argv[2]:
    raise SystemExit("manifest tag mismatch")
print(payload["bundle"]["name"])
' "$manifest" "$tag")"
commit_sha="$(python3 -c '
import json, pathlib, sys
payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
print(payload["commit_sha"])
' "$manifest")"
[[ "$bundle_name" =~ ^trace-marketing-macos-arm64-v[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz$ ]] || \
    die "manifest bundle name is invalid"
[[ "$commit_sha" =~ ^[0-9a-f]{40}$ ]] || die "manifest commit SHA is invalid"
gh release download "$tag" --repo "$REPOSITORY" --dir "$download_directory" \
    --pattern "$bundle_name"
bundle="$download_directory/$bundle_name"
[[ -f "$bundle" ]] || die "release bundle is missing"

for asset in "$manifest" "$bootstrap" "$bundle"; do
    gh attestation verify "$asset" \
        --repo "$REPOSITORY" \
        --signer-workflow "$REPOSITORY/.github/workflows/release-mac-worker.yml" \
        --source-ref refs/heads/main \
        --source-digest "$commit_sha" \
        --deny-self-hosted-runners >/dev/null
done

python3 "$bootstrap" \
    --manifest "$manifest" \
    --bundle "$bundle" \
    --home "$agent_home" \
    --install-root "$install_root" \
    --uv "$uv_path" \
    --gh "$(command -v gh)" \
    --interval-seconds "$interval_seconds"
