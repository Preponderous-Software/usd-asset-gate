#!/usr/bin/env bash
# usd-asset-gate — copy-in CI script (local-only mode, no third-party actions).
#
# Copyright (c) 2026 Daniel McCoy Stephenson. MIT License:
# https://github.com/Preponderous-Software/usd-asset-gate/blob/main/LICENSE
#
# Copy this file into your repository (for example to ci/usd-asset-gate.sh) and run
# it from a workflow `run:` step after a plain-git checkout. It installs usd-core and
# usd-asset-gate into a private virtualenv, checks the USD files the pull request
# changed, writes annotations and a job summary, updates the single PR comment, and
# exits with the check's exit code (0 pass, 1 findings, 2 usage/config error).
#
# Environment (all optional):
#   BASE_SHA                  base commit of the pull request; when set, only USD files
#                             changed since it are checked (git diff BASE_SHA...HEAD)
#   USD_ASSET_GATE_PATHS      space-separated files/directories to check instead of
#                             (or, with BASE_SHA, to narrow) the changed files
#   USD_ASSET_GATE_CONFIG     config file (default: .usd-asset-gate.yml if present)
#   USD_ASSET_GATE_FAIL_ON    error | warning | info | never (default: from the config)
#   USD_ASSET_GATE_COMMENT    "true" (default) to create/update the PR comment
#   USD_CORE_VERSION          usd-core version to install (default 26.8)
#   USD_ASSET_GATE_SPEC       what pip installs for usd-asset-gate: a version pin, a
#                             path to a vendored source tree, or a git URL pinned to a
#                             commit (default: the upstream repository at main — pin it)
#   GITHUB_TOKEN              used to fetch BASE_SHA if missing, and for the comment
#   PYTHON                    interpreter used to create the virtualenv (default python3)
#
# Advisory visual diffs (RFC 0028; never change the exit code):
#   USD_ASSET_GATE_PREVIEW    "auto" (default): with BASE_SHA set and `preview.enabled: true`
#                             in the config, render the changed files before and after with
#                             Strata, add "Visual changes" to the job summary and the PR
#                             comment, and keep the PNGs in $RUNNER_TEMP/usd-asset-gate-previews.
#                             "false" turns it off.
#   STRATA_LIBRARY            a libstrata.so to use; otherwise the release below is
#                             downloaded and checked against its pinned SHA-256
#   STRATA_VERSION            Strata release to download (default 0.5.0)
#   STRATA_SHA256             SHA-256 of libstrata-$STRATA_VERSION-linux-x64.so from that
#                             release's SHA256SUMS (default: the 0.5.0 value)
# The preview needs EGL and a Mesa driver on the runner (for example
# `sudo apt-get install -y libegl1 libegl-mesa0 libgl1-mesa-dri`); without them it reports
# "preview unavailable" and the check is unaffected.
set -uo pipefail

USD_CORE_VERSION="${USD_CORE_VERSION:-26.8}"
SPEC="${USD_ASSET_GATE_SPEC:-git+https://github.com/Preponderous-Software/usd-asset-gate@main}"
COMMENT="${USD_ASSET_GATE_COMMENT:-true}"
PYTHON="${PYTHON:-python3}"
WORK="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
VENV="$WORK/usd-asset-gate-venv"
REPORT="$WORK/usd-asset-gate-report.json"
SUMMARY="${GITHUB_STEP_SUMMARY:-}"
PREVIEW="${USD_ASSET_GATE_PREVIEW:-auto}"
PREVIEWS="$WORK/usd-asset-gate-previews.json"
PREVIEW_DIR="$WORK/usd-asset-gate-previews"
STRATA_VERSION="${STRATA_VERSION:-0.5.0}"
STRATA_SHA256="${STRATA_SHA256:-44f8263426b546c04295b1033032fded97541060c8e804873894d43e80fcffcb}"

log() { printf 'usd-asset-gate.sh: %s\n' "$*"; }

# Run git with the token as a one-off http.extraheader; it is never written to .git/config.
git_with_token() {
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    local basic
    basic=$(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 | tr -d '\n')
    git -c "http.extraheader=AUTHORIZATION: basic $basic" "$@"
  else
    git "$@"
  fi
}

# 1. Make sure the base commit (and a merge base with HEAD) is available.
if [ -n "${BASE_SHA:-}" ]; then
  if ! git cat-file -e "${BASE_SHA}^{commit}" 2>/dev/null; then
    log "fetching base commit $BASE_SHA"
    git_with_token fetch -q --no-tags origin "$BASE_SHA" || {
      log "could not fetch $BASE_SHA"; exit 2; }
  fi
  if ! git merge-base "$BASE_SHA" HEAD >/dev/null 2>&1 \
     && [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
    log "shallow clone without a merge base; fetching full history"
    git_with_token fetch -q --no-tags --unshallow origin || true
  fi
fi

# 2. Install into a private virtualenv (system Pythons may refuse pip installs).
if [ ! -x "$VENV/bin/usd-asset-gate" ]; then
  if [ "$SPEC" = "git+https://github.com/Preponderous-Software/usd-asset-gate@main" ]; then
    log "installing usd-asset-gate from main; set USD_ASSET_GATE_SPEC to pin a commit"
  fi
  "$PYTHON" -m venv "$VENV" || { log "could not create a virtualenv with $PYTHON"; exit 2; }
  "$VENV/bin/python" -m pip install -q --disable-pip-version-check \
    "usd-core==$USD_CORE_VERSION" "$SPEC" || { log "pip install failed"; exit 2; }
fi

# 3. Check.
args=(check --format github --json "$REPORT")
[ -n "$SUMMARY" ] && args+=(--summary "$SUMMARY")
[ -n "${USD_ASSET_GATE_CONFIG:-}" ] && args+=(--config "$USD_ASSET_GATE_CONFIG")
[ -n "${USD_ASSET_GATE_FAIL_ON:-}" ] && args+=(--fail-on "$USD_ASSET_GATE_FAIL_ON")
[ -n "${BASE_SHA:-}" ] && args+=(--changed-since "$BASE_SHA")
read -r -a paths <<< "${USD_ASSET_GATE_PATHS:-}"
if [ "${#paths[@]}" -gt 0 ]; then
  args+=("${paths[@]}")
elif [ -z "${BASE_SHA:-}" ]; then
  args+=(.)
fi
"$VENV/bin/usd-asset-gate" "${args[@]}"
status=$?

# 4. Preview (advisory: every failure here is a warning, never the exit code).
rm -f "$PREVIEWS"
preview_enabled() {
  "$VENV/bin/python" - "${USD_ASSET_GATE_CONFIG:-}" <<'PY' 2>/dev/null
import sys
from usd_asset_gate.config import load
config = load(sys.argv[1] or None)
sys.exit(0 if config.preview.get("enabled", False) else 1)
PY
}
fetch_strata() {
  if [ -n "${STRATA_LIBRARY:-}" ]; then
    return 0
  fi
  local so="$WORK/libstrata-$STRATA_VERSION-linux-x64.so"
  if [ ! -f "$so" ]; then
    curl -fsSL -o "$so.part" \
      "https://github.com/Preponderous-Software/strata/releases/download/v$STRATA_VERSION/libstrata-$STRATA_VERSION-linux-x64.so" \
      || { rm -f "$so.part"; return 1; }
    mv "$so.part" "$so"
  fi
  if ! echo "$STRATA_SHA256  $so" | sha256sum -c - >/dev/null 2>&1; then
    rm -f "$so"
    log "libstrata-$STRATA_VERSION-linux-x64.so does not match the pinned SHA-256; deleted"
    return 1
  fi
  export STRATA_LIBRARY="$so"
}
if [ "$PREVIEW" != "false" ] && [ -n "${BASE_SHA:-}" ] && [ "$status" -ne 2 ] && preview_enabled; then
  log "preview: rendering changed files with Strata (advisory)"
  if ! "$VENV/bin/python" -c "import numpy" 2>/dev/null; then
    "$VENV/bin/python" -m pip install -q --disable-pip-version-check "numpy>=1.24" \
      || log "preview: could not install numpy"
  fi
  fetch_strata || log "preview: could not get Strata $STRATA_VERSION"
  pargs=(preview --changed-since "$BASE_SHA" --out "$PREVIEW_DIR" --json "$PREVIEWS")
  [ -n "$SUMMARY" ] && pargs+=(--summary "$SUMMARY")
  [ -n "${USD_ASSET_GATE_CONFIG:-}" ] && pargs+=(--config "$USD_ASSET_GATE_CONFIG")
  [ "${#paths[@]}" -gt 0 ] && pargs+=("${paths[@]}")
  LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}" GALLIUM_DRIVER="${GALLIUM_DRIVER:-llvmpipe}" \
    "$VENV/bin/usd-asset-gate" "${pargs[@]}" \
    || echo "::warning title=usd-asset-gate::the advisory preview did not run; see the log"
fi

# 5. Comment (never changes the exit code; it skips itself on fork PRs).
if [ "$COMMENT" = "true" ] && [ -f "$REPORT" ] && [ "$status" -ne 2 ]; then
  cargs=(comment --from "$REPORT")
  [ -f "$PREVIEWS" ] && cargs+=(--previews "$PREVIEWS")
  if ! "$VENV/bin/usd-asset-gate" "${cargs[@]}"; then
    echo "::warning title=usd-asset-gate::the PR comment could not be posted; see the log"
  fi
fi

exit "$status"
