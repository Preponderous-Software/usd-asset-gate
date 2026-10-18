# usd-asset-gate — Concept

> Status: planning. Nothing here is implemented yet. Facts about OpenUSD and other tools
> below were checked on 2026-10-17 against the sources linked next to them.

## The problem

OpenUSD files (`.usd`, `.usda`, `.usdc`, `.usdz`) are increasingly kept in git next to the
code that consumes them. Code in those repositories gets linted, type-checked and tested on
every pull request; the assets usually get nothing. The defects that slip through are dull
and expensive:

- a reference or sublayer that points at a file which was never committed, or that only
  resolves on the author's machine;
- a stage with no `defaultPrim`, so referencing it without a prim path silently brings in
  nothing;
- missing or wrong `metersPerUnit` / `upAxis`, so an asset arrives 100x too large or lying on
  its side;
- a "small prop" that turns out to carry 400,000 prims or no instancing at all;
- prim names that break a pipeline's naming convention, or a 900 MB layer committed by
  accident.

OpenUSD already ships a validator for much of this — `usdchecker` and the validation
framework underneath it — but wiring it into CI well is left to every team. The obvious
approach (copy a checker script into `.github/`, pin `usd-core`, loop over changed files)
breaks in quiet ways, as the prior-art section shows, and offers no way to add house rules,
no annotations on the diff, and no readable summary on the pull request.

## What it is

`usd-asset-gate` is a CI quality gate for OpenUSD assets, delivered two ways:

1. **A plain CLI** (`usd-asset-gate check …`) that runs anywhere Python and `usd-core` run —
   a laptop, a pre-commit hook, GitLab CI, Jenkins, a GitHub runner.
2. **A GitHub Action** that wraps the CLI: on a pull request it checks only the changed USD
   files, emits inline annotations, writes a job summary, and keeps **one** summary comment
   on the PR, updated in place on every push.

Each run combines:

- **OpenUSD's own validators** — the same validation framework `usdchecker` uses — run
  in-process through the `pxr.UsdValidation` Python API; and
- **configurable house rules** — stage metadata values, file-size limits, prim-count and
  instance budgets, naming conventions — set in a YAML file in the consumer repository.

## Who it is for

- Teams that keep USD assets in git: games and real-time projects, VFX/animation pipelines,
  robotics and simulation scenes, digital-twin and product-visualisation repositories.
- Maintainers of public USD sample-asset collections who want contributions checked
  automatically.
- Tool authors whose exporters write USD and who want a regression gate on their golden
  output files.

## What makes it interesting

- **It uses the upstream validators, not a re-implementation.** Correctness of "is this a
  valid USD asset" stays with the OpenUSD project. This tool adds policy, diff-awareness and
  reporting around it.
- **It is built for locked-down CI.** Some organisations allow only actions defined in their
  own repositories (no third-party `uses:` lines). The gate is designed so that the whole
  thing can run from a copied-in script with `pip install usd-core` and a plain `git fetch`,
  with no `actions/checkout`, no changed-files action and no comment-posting action. The
  March 2025 compromise of a widely used changed-files action
  ([CVE-2025-30066](https://github.com/advisories/GHSA-mrrh-fwg8-r2c3)) is a concrete
  example of why such policies exist.
- **It is quiet by design.** One comment per pull request, edited in place; annotations only
  on files the PR touched; budgets expressed as numbers a reviewer can argue about.

## Non-goals

- **Not a new validator framework.** Upstream validators are consumed as they are; custom
  checks that belong in OpenUSD should be proposed there.
- **No auto-fixing in CI.** The OpenUSD framework exposes fixers
  (`ValidationError.GetFixers()`), but a CI gate that rewrites binary assets on a branch is
  out of scope. Reports may mention that a fixer exists.
- **No render-quality judgement.** Thumbnails, if added, are a visual aid for reviewers, not
  an image-diff test.
- **No asset management.** No storage, versioning, LFS handling or asset-resolver plugins
  beyond OpenUSD's default resolver.
- **Not a schema or profile authoring tool.** Industry profiles (for example NVIDIA's
  SimReady specifications) are out of scope for the MVP.

## Prior art

| Tool | What it is | How usd-asset-gate differs |
|---|---|---|
| [`usdchecker`](https://openusd.org/release/toolset.html#usdchecker) (OpenUSD) | Pixar's command-line checker; validates a stage or `.usdz` package. Since 25.02 it is a C++ tool at `pxr/usdValidation/bin/usdchecker` built on the validation framework; 25.08 made that framework the default; 26.03 removed the old compliance checker from it; 26.05 removed `--arkit` ([CHANGELOG](https://github.com/PixarAnimationStudios/OpenUSD/blob/v26.08/CHANGELOG.md)). Options include `--includeKeywords`, `--variantSets`, `--variants`, `--noAssetChecks`, `--strict`. | The foundation, not a competitor. Note that the PyPI `usd-core` wheel (26.8, checked) ships the `pxr` Python modules but **no `usdchecker` executable**, so CI on a stock runner cannot just call it. usd-asset-gate calls the same validators in-process and adds house rules, diff scoping and CI reporting. |
| [OpenUSD validation framework](https://openusd.org/release/api/md_pxr_usd_validation_usd_validation__r_e_a_d_m_e.html) (`UsdValidation`) | Registry of named validators (`plugin:validatorName`) run on layers, stages or prims, selected by keyword, schema type or plugin; supports fixers. | Consumed directly via `pxr.UsdValidation.ValidationContext`. `usd-core` 26.8 registers 28 validators, among them `usdValidation:StageMetadataChecker` (defaultPrim), `usdGeomValidators:StageMetadataChecker` (upAxis, metersPerUnit), `usdUtilsValidators:MissingReferenceValidator` and `usdValidation:CompositionErrorTest` — verified by running them on a broken fixture. |
| `UsdUtils.ComplianceChecker` (Python) | The older Python checker that `usdchecker.py` was built on. | Deprecated, then **removed in OpenUSD 26.08** ([CHANGELOG](https://github.com/PixarAnimationStudios/OpenUSD/blob/v26.08/CHANGELOG.md)). Anything vendoring the old `usdchecker.py` stops working on current `usd-core`. Not used here. |
| [NVIDIA `usd-validation-nvidia`](https://github.com/NVIDIA-Omniverse/usd-validation-nvidia) — the [self-described standalone successor](https://github.com/NVIDIA-Omniverse/usd-validation-nvidia/blob/main/validation/README.md) to the [Omniverse Asset Validator](https://docs.omniverse.nvidia.com/kit/docs/asset-validator/latest/index.html) (PyPI [`omniverse-asset-validator`](https://pypi.org/project/omniverse-asset-validator/)) | An extensible validation engine with a CLI (`nvidia_usd_validate`), many extra rules, profiles, JSON/CSV reports and automatic fixing (`--fix`). Source under Apache-2.0, docs under CC-BY-4.0. | A richer rule library, but not a CI integration: no diff scoping, PR annotations, job summaries or in-place PR comment, and no YAML house-rule config of the kind described here. usd-asset-gate could later accept it as an optional extra rule source (see ARCHITECTURE.md open questions). |
| [`usd-wg/assets` PR validation](https://github.com/usd-wg/assets/blob/main/.github/workflows/PR-validation.yml) | The USD Working Group sample-assets repo runs a vendored `usdchecker.py` over changed files with `usd-core==24.03`. | The pattern this project generalises. It relies on two third-party actions (`actions/checkout`, `tj-actions/changed-files`), pins an old `usd-core` because its vendored script depends on `ComplianceChecker`, and reports only through the job log. |
| GitHub Marketplace | No dedicated OpenUSD validation action was found in a search on 2026-10-17. | — |

## Success looks like

A repository adds one YAML file and one workflow (or one copied script), and from then on a
pull request that breaks a reference, drops the default prim or blows a prim budget shows a
red check, an annotation on the offending file, and a single, current summary comment.
