#!/usr/bin/env bash

# shellcheck disable=SC2218
# Helper functions are defined before the runtime dispatch; this warning is a
# false positive for the command-substitution and callback boundaries here.

set -Eeuo pipefail

readonly PACKAGE_NAME="trace-appium-capture"
readonly CLI_NAME="trace-ads"
readonly REPOSITORY_URL="${TRACE_ADS_REPOSITORY:-https://github.com/corca-ai/ads-booster.git}"
readonly DEFAULT_REF="${TRACE_ADS_REF:-main}"
readonly PYTHON_VERSION="${TRACE_ADS_PYTHON:-3.14}"
readonly HOME_DIRECTORY="${HOME:?HOME is required}"

bin_directory="${TRACE_ADS_BIN_DIR:-$HOME_DIRECTORY/.local/bin}"
source_override="${TRACE_ADS_SOURCE:-}"
workspace_name="${TRACE_AGENT_WORKSPACE_NAME:-}"
ref="$DEFAULT_REF"
ref_was_set=0
dry_run=0
shell_update=1
workspace_service=0
cloudflared_install=1
resolved_source=""
uv_path=""
shell_rc=""

die() {
    printf 'trace-ads installer: %s\n' "$*" >&2
    exit 1
}

info() {
    printf 'trace-ads installer: %s\n' "$*" >&2
}

print_help() {
    cat <<'EOF'
Install trace-ads into a user-owned uv tool environment. This installer installs the
CLI only; start the workspace later with `trace-agent workspace start`. Native capture
prerequisites remain manual: Appium with XCUITest, Xcode Simulator, and a Trace_iOS
Debug build installed as com.corca.Trace.

Usage:
  install.sh [options]

Options:
  --source <path-or-url>  Install from a local checkout or package source.
  --ref <git-ref>         Git ref for the default GitHub source (default: main).
  --bin-dir <path>        User bin directory (default: ~/.local/bin).
  --dry-run               Print the install plan without changing the system.
  --no-shell-update       Do not append the bin directory to zsh/bash startup files.
  --workspace-service     Also start the macOS launchd workspace service now.
  --no-workspace-service  Keep the workspace service stopped (default).
  --no-cloudflared-install Do not install cloudflared automatically when missing.
  --workspace-name <name> Workspace name for first-time workspace setup.
  -h, --help              Show this help.

Environment:
  TRACE_ADS_REPOSITORY     Git repository URL override.
  TRACE_ADS_REF            Default Git ref override.
  TRACE_ADS_SOURCE         Source override, equivalent to --source.
  TRACE_ADS_BIN_DIR        Bin directory override, equivalent to --bin-dir.
  TRACE_ADS_PYTHON         Python version passed to uv (default: 3.14).

Examples:
  curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/corca-ai/ads-booster/main/install.sh | bash
  bash install.sh --source .
  bash install.sh --ref v0.1.0 --no-shell-update
EOF
}

require_absolute_path() {
    case "$1" in
        /*) ;;
        *) die "--bin-dir must be an absolute path: $1" ;;
    esac
}

local_checkout_source() {
    local script_name="${BASH_SOURCE[0]:-}"
    local script_directory=""

    if [[ -z "$script_name" || ! -f "$script_name" ]]; then
        return 1
    fi

    script_directory="$(cd -- "$(dirname -- "$script_name")" && pwd -P)"
    if [[ -f "$script_directory/pyproject.toml" ]]; then
        printf '%s\n' "$script_directory"
        return 0
    fi
    return 1
}

resolve_source() {
    local checkout_source=""

    if [[ -n "$source_override" ]]; then
        case "$source_override" in
            .|./*|../*|/*)
                [[ -f "$source_override/pyproject.toml" ]] || die "source has no pyproject.toml: $source_override"
                (cd -- "$source_override" && pwd -P)
                ;;
            *)
                printf '%s\n' "$source_override"
                ;;
        esac
        return
    fi

    checkout_source="$(local_checkout_source || true)"
    if [[ -n "$checkout_source" && "$ref_was_set" == "0" ]]; then
        printf '%s\n' "$checkout_source"
        return
    fi

    case "$REPOSITORY_URL" in
        git+*) printf '%s@%s\n' "$REPOSITORY_URL" "$ref" ;;
        *) printf 'git+%s@%s\n' "$REPOSITORY_URL" "$ref" ;;
    esac
}

find_uv() {
    local discovered=""
    discovered="$(command -v uv || true)"
    if [[ -n "$discovered" ]]; then
        printf '%s\n' "$discovered"
        return 0
    fi

    if [[ "$dry_run" == "1" ]]; then
        printf '%s\n' "uv (installed by Astral's official installer)"
        return 0
    fi

    command -v curl >/dev/null 2>&1 || die "uv is missing and curl is not available; install uv first"
    mkdir -p "$bin_directory"
    info "uv not found; installing uv into $bin_directory"
    if ! (export UV_INSTALL_DIR="$bin_directory"; curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 https://astral.sh/uv/install.sh | sh) >&2; then
        die "uv installation failed"
    fi

    if [[ -x "$bin_directory/uv" ]]; then
        printf '%s\n' "$bin_directory/uv"
        return 0
    fi

    discovered="$(command -v uv || true)"
    [[ -n "$discovered" ]] || die "uv was installed but could not be found"
    printf '%s\n' "$discovered"
}

print_plan() {
    printf 'trace-ads installer (dry run)\n'
    printf '  source: %s\n' "$resolved_source"
    printf '  python: %s\n' "$PYTHON_VERSION"
    printf '  bin directory: %s\n' "$bin_directory"
    printf '  command: UV_TOOL_BIN_DIR=%q %q tool install --force --python %q --from %q %q\n' \
        "$bin_directory" "$uv_path" "$PYTHON_VERSION" "$resolved_source" "$PACKAGE_NAME"
    if [[ "$shell_update" == "1" ]]; then
        printf '  shell PATH: update zsh/bash startup file when supported\n'
    else
        printf '  shell PATH: unchanged\n'
    fi
    if [[ "$workspace_service" == "1" ]]; then
        printf '  workspace service: macOS launchd + cloudflared tunnel\n'
        if [[ -n "$workspace_name" ]]; then
            printf '  workspace name: %s\n' "$workspace_name"
        else
            printf '  workspace name: prompt on first macOS setup\n'
        fi
        if [[ "$cloudflared_install" == "1" ]]; then
            printf '  cloudflared: install with Homebrew on macOS when missing\n'
        else
            printf '  cloudflared: automatic install disabled\n'
        fi
    else
        printf '  workspace service: not started (run trace-agent workspace start)\n'
    fi
}

ensure_cloudflared() {
    if [[ "$workspace_service" != "1" || "$cloudflared_install" != "1" ]]; then
        return
    fi
    if command -v cloudflared >/dev/null 2>&1; then
        return
    fi

    case "$(uname -s)" in
        Darwin)
            if ! command -v brew >/dev/null 2>&1; then
                die "cloudflared is missing; install Homebrew or use --no-workspace-service"
            fi
            info "cloudflared not found; installing it with Homebrew"
            brew install cloudflared
            command -v cloudflared >/dev/null 2>&1 || die "cloudflared installation failed"
            ;;
        *)
            info "cloudflared auto-install is supported on macOS only; local fallback remains available"
            ;;
    esac
}

configure_shell_path() {
    local shell_name="${SHELL##*/}"
    local marker="# trace-ads installer"
    local escaped_bin_directory=""
    local path_line=""

    if [[ "$shell_update" != "1" ]]; then
        return
    fi

    case "$shell_name" in
        bash) shell_rc="$HOME_DIRECTORY/.bashrc" ;;
        zsh) shell_rc="$HOME_DIRECTORY/.zshrc" ;;
        *) return ;;
    esac

    escaped_bin_directory="$(printf '%q' "$bin_directory")"
    path_line="export PATH=${escaped_bin_directory}:\$PATH"
    if [[ -f "$shell_rc" ]] && grep -Fqx "$path_line" "$shell_rc"; then
        return
    fi

    if [[ ! -f "$shell_rc" ]] || ! grep -Fqx "$marker" "$shell_rc"; then
        printf '\n%s\n' "$marker" >> "$shell_rc"
    fi
    printf '%s\n' "$path_line" >> "$shell_rc"
}

while (($# > 0)); do
    case "$1" in
        --source|--from)
            (($# >= 2)) || die "$1 requires a value"
            source_override="$2"
            shift 2
            ;;
        --source=*|--from=*)
            source_override="${1#*=}"
            shift
            ;;
        --workspace-name)
            (($# >= 2)) || die "--workspace-name requires a value"
            workspace_name="$2"
            shift 2
            ;;
        --workspace-name=*)
            workspace_name="${1#*=}"
            shift
            ;;
        --ref)
            (($# >= 2)) || die "--ref requires a value"
            ref="$2"
            ref_was_set=1
            shift 2
            ;;
        --ref=*)
            ref="${1#*=}"
            ref_was_set=1
            shift
            ;;
        --bin-dir)
            (($# >= 2)) || die "--bin-dir requires a value"
            bin_directory="$2"
            shift 2
            ;;
        --bin-dir=*)
            bin_directory="${1#*=}"
            shift
            ;;
        --dry-run)
            dry_run=1
            shift
            ;;
        --no-shell-update)
            shell_update=0
            shift
            ;;
        --no-workspace-service)
            workspace_service=0
            shift
            ;;
        --workspace-service)
            workspace_service=1
            shift
            ;;
        --no-cloudflared-install)
            cloudflared_install=0
            shift
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

require_absolute_path "$bin_directory"
resolved_source="$(resolve_source)"
uv_path="$(find_uv)"

if [[ "$dry_run" == "1" ]]; then
    print_plan
    exit 0
fi

mkdir -p "$bin_directory"
info "installing $PACKAGE_NAME from $resolved_source"
UV_TOOL_BIN_DIR="$bin_directory" "$uv_path" tool install \
    --force \
    --python "$PYTHON_VERSION" \
    --from "$resolved_source" \
    "$PACKAGE_NAME"

export PATH="$bin_directory:$PATH"
[[ -x "$bin_directory/$CLI_NAME" ]] || die "installation completed but $CLI_NAME was not created in $bin_directory"
"$bin_directory/$CLI_NAME" --help >/dev/null || die "$CLI_NAME verification failed"
configure_shell_path
ensure_cloudflared

if [[ "$workspace_service" == "1" && "$(uname -s)" == "Darwin" ]]; then
    agent_home="${TRACE_AGENT_HOME:-$HOME_DIRECTORY/.trace-agent}"
    if [[ ! -f "$agent_home/service.json" && -z "$workspace_name" ]]; then
        if [[ -r /dev/tty ]]; then
            read -r -p "Workspace name: " workspace_name < /dev/tty
        else
            die "first workspace setup needs --workspace-name or TRACE_AGENT_WORKSPACE_NAME"
        fi
    fi
fi

if [[ "$workspace_service" == "1" ]]; then
    case "$(uname -s)" in
        Darwin)
            info "installing and starting the macOS workspace service with cloudflared"
            service_args=(service install --tunnel cloudflared)
            if [[ -n "$workspace_name" ]]; then
                service_args+=(--workspace-name "$workspace_name")
            fi
            if ! "$bin_directory/trace-agent" "${service_args[@]}"; then
                die "workspace service installation failed"
            fi
            ;;
        *)
            info "workspace service auto-start skipped: launchd is available only on macOS"
            ;;
    esac
else
    info "workspace service not started; run trace-agent workspace start when ready"
fi

printf '\nInstalled %s\n' "$CLI_NAME"
printf '  executable: %s/%s\n' "$bin_directory" "$CLI_NAME"
if [[ -n "$shell_rc" ]]; then
    printf '  PATH file: %s\n' "$shell_rc"
    printf '  reload: source %s\n' "$shell_rc"
else
    printf "  current shell: export PATH=%q:\$PATH\n" "$bin_directory"
fi
printf '  verify: %s --help\n' "$CLI_NAME"
