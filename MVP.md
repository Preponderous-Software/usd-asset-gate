# usd-asset-gate — MVP

> Status: implemented — milestones M1–M8 are done (see the issues). See [CONCEPT.md](CONCEPT.md) for the why and
> [ARCHITECTURE.md](ARCHITECTURE.md) for the how.

## The smallest end-to-end slice

A consumer repository adds `.usd-asset-gate.yml` and a workflow. A pull request changes
`props/crate.usda`, which references a file that was never committed and has 12,000 prims
against a budget of 5,000. The workflow:

1. checks out the PR with plain `git` (no `actions/checkout` required);
2. installs `usd-core` and `usd-asset-gate` with `pip`;
3. finds the USD files the PR changed;
4. runs the OpenUSD validators and the configured house rules on each;
5. fails the check, puts an error annotation on `props/crate.usda`, writes a job summary
   table, and creates — or, on the next push, edits — one PR comment listing both findings.

The same run works locally: `usd-asset-gate check props/crate.usda` prints the same findings
and exits non-zero.

## In scope

- Python CLI `usd-asset-gate` with `check` (files, directories, or "changed since `<ref>`")
  and `rules` (list every available rule and its effective setting).
- Upstream checks via `pxr.UsdValidation` (the framework behind `usdchecker`), selectable by
  validator name or keyword.
- House rules: required stage metadata values (`metersPerUnit`, `upAxis`), default prim
  present, file-size limit, prim-count and instance budgets, prim naming patterns.
  Unresolved references and sublayers are covered by the upstream validators and surfaced
  under friendly rule names.
- Configuration in `.usd-asset-gate.yml`: enable/disable, severity, parameters, per-path
  overrides, ignore globs.
- Outputs: human text, JSON, GitHub workflow-command annotations, `$GITHUB_STEP_SUMMARY`
  Markdown, and one in-place PR comment.
- Two ways to run in GitHub Actions: a composite action in this repo, and a
  **local-only mode** — a copy-in shell script plus example workflow with no third-party
  `uses:` lines.
- Fixture assets (pass and fail) and tests for every rule and output.

## Out of scope (for the MVP)

- Thumbnail rendering (needs Hydra/imaging, which the `usd-core` wheel does not include).
  Planned for after the MVP in a full OpenUSD container — see
  [ARCHITECTURE.md](ARCHITECTURE.md#thumbnails-full-openusd-container).
- Auto-fixing, including OpenUSD fixers.
- Variant-by-variant validation controls beyond the framework defaults.
- Custom asset resolvers, Git LFS-specific handling, Omniverse/Nucleus paths.
- Third-party rule libraries (e.g. NVIDIA `usd-validation-nvidia`) as rule sources. NVIDIA's
  validator is planned as an optional post-MVP extra (`usd-asset-gate[nvidia]`).
- Publishing to PyPI and the GitHub Marketplace. The code should be ready for it; publishing
  happens after the MVP (see [After the MVP](#after-the-mvp)).

## Acceptance criteria

1. `pip install .` in a clean Python 3.10–3.13 virtualenv with `usd-core` installed provides
   a working `usd-asset-gate` command.
2. Every fixture under `tests/fixtures/pass/` exits 0 with the default config; every fixture
   under `tests/fixtures/fail/` exits 1 and reports exactly the rule IDs listed in its
   sidecar `expected.json`.
3. A missing sublayer, a missing reference, a missing `defaultPrim`, a missing
   `metersPerUnit` and a missing `upAxis` are each reported, with the file path and, where
   known, the prim path.
4. A config that sets `metersPerUnit: 0.01` fails a stage authored at `1.0`; a
   `max_prims: 100` budget fails a stage with 101 prims and passes one with 100.
5. An invalid config file (unknown rule, wrong type) exits 2 with a message naming the key.
6. With `--format github`, the output is valid GitHub workflow commands; with
   `--summary $GITHUB_STEP_SUMMARY`, a Markdown table is appended.
7. Running the comment step twice on the same PR leaves exactly one bot comment, whose body
   reflects the second run.
8. The example local-only workflow contains no `uses:` lines and passes on this repository's
   own fixtures in CI.
9. `.usdz` packages and `.usdc` crate files are checked as well as `.usda`.

## Milestones

Each milestone is one GitHub issue (label `mvp`). Order is the intended build order.

### M1 — Project skeleton, CLI and finding model

Issue: #1

`pyproject.toml`, package `usd_asset_gate`, `usd-asset-gate` entry point with `check` and
`rules` subcommands, the `Finding` data model, exit codes (0 pass, 1 findings at or above
the fail threshold, 2 usage/config error), text and JSON output, pytest + a CI workflow for
this repo, and a `usd-core` version guard with a clear error when `pxr` is missing.

### M2 — Upstream OpenUSD validation adapter

Issue: #2

Run `pxr.UsdValidation` validators on each stage through `ValidationContext`, map every
`ValidationError` (name, type, message, sites, validator) to a `Finding`, select validators
by name or keyword, capture composition warnings that OpenUSD prints instead of raising,
and surface the presence of fixers as a hint. Fixtures for missing reference, missing
sublayer, missing default prim, missing `upAxis`/`metersPerUnit`.

### M3 — Configuration file and rule engine

Issue: #3

`.usd-asset-gate.yml` loader and schema validation, rule registry (upstream + house),
per-rule `enabled`/`severity`/parameters, `fail_on` threshold, `include`/`exclude` globs,
per-path `overrides`, and the `rules` subcommand printing the effective configuration.

### M4 — House rules: stage metadata and file limits

Issue: #4

`stage-metadata` (required `metersPerUnit` / `upAxis` values, optional tolerance),
`default-prim` (present, optionally a given `kind`), `file-size` (max bytes per extension),
and friendly aliases `unresolved-references` / `unresolved-sublayers` over the upstream
validators.

### M5 — House rules: budgets and naming conventions

Issue: #5

`prim-budget` (total and per-type prim counts), `instance-budget` (minimum instancing ratio
or maximum prototypes, from `UsdUtils.ComputeUsdStageStats`), and `prim-naming` (regex
per prim type or path glob). Fixtures at and just over each limit.

### M6 — Changed-file discovery and CI outputs

Issue: #6

`check --changed-since <ref>` using `git diff --name-only --diff-filter=ACMR`, expanding to
the USD files the change touched; GitHub annotations (`::error file=…::`); job summary
Markdown; annotation and summary size limits handled.

### M7 — Single in-place PR comment

Issue: #7

Create-or-update one PR comment identified by a hidden marker, using the REST API through
the Python standard library and `GITHUB_TOKEN`; skip cleanly (with a log line, not a
failure) when the token cannot write, such as on pull requests from forks. On fork PRs the
results are still shown in the job log, annotations and job summary; `pull_request_target`
is not used.

### M8 — Distribution: composite action and local-only mode

Issue: #8

`action.yml` composite action; `ci/usd-asset-gate.sh` copy-in script that does a plain
`git fetch` checkout, `pip install`, check, summary and comment; example workflows for both
modes; documentation; this repository's own CI dogfooding the local-only workflow on its
fixtures.

## After the MVP

Not MVP milestones and not tracked as `mvp` issues; started only once the acceptance
criteria above are met. Owner decisions of 2026-10-17, detailed in
[ARCHITECTURE.md](ARCHITECTURE.md#post-mvp-roadmap):

- **Distribution** — publish `usd-asset-gate` to PyPI and list the composite action on the
  GitHub Marketplace.
- **Thumbnails** — optional thumbnail step running `usdrecord` in a Docker image with a full,
  version-pinned OpenUSD build, fetched or built with plain `docker` commands (no `uses:`).
- **Preview (advisory)** — owner decision of 2026-10-23 (RFC 0028): `usd-asset-gate preview`
  renders before/after images of changed assets headless with Strata and diffs them; never
  fails the check. A light tier beside the thumbnail container, not a replacement. Detailed in
  [ARCHITECTURE.md](ARCHITECTURE.md#preview-strata-headless-advisory).
- **NVIDIA rule source** — `usd-validation-nvidia` as an optional extra
  (`pip install usd-asset-gate[nvidia]`); core runtime dependencies stay `usd-core` + `PyYAML`.
