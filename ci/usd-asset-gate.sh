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
set -uo pipefail

USD_CORE_VERSION="${USD_CORE_VERSION:-26.8}"
SPEC="${USD_ASSET_GATE_SPEC:-git+https://github.com/Preponderous-Software/usd-asset-gate@main}"
COMMENT="${USD_ASSET_GATE_COMMENT:-true}"
PYTHON="${PYTHON:-python3}"
WORK="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
VENV="$WORK/usd-asset-gate-venv"
REPORT="$WORK/usd-asset-gate-report.json"
SUMMARY="${GITHUB_STEP_SUMMARY:-}"

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

# 4. Comment (never changes the exit code; it skips itself on fork PRs).
if [ "$COMMENT" = "true" ] && [ -f "$REPORT" ] && [ "$status" -ne 2 ]; then
  if ! "$VENV/bin/usd-asset-gate" comment --from "$REPORT"; then
    echo "::warning title=usd-asset-gate::the PR comment could not be posted; see the log"
  fi
fi

exit "$status"
