# usd-asset-gate

**usd-asset-gate** is a **CI quality gate for OpenUSD assets** — the check that stands between a broken `.usd` file and your main branch.  
It runs OpenUSD's own validators plus your **house rules** on every pull request that touches `.usd`, `.usda`, `.usdc` or `.usdz` files, and reports the results where reviewers already look.

It ships as a **plain CLI** that runs anywhere Python runs, and as a **GitHub Action** that can also be used in organisations that allow no third-party actions.

> **Status: in development — not yet usable as a gate.** The package skeleton, CLI, finding model and text/JSON output (milestone M1), the in-process OpenUSD validator adapter (M2) and the `.usd-asset-gate.yml` configuration with its rule engine and aliases (M3), the stage-metadata, default-prim kind and file-size house rules (M4), the budget and naming rules (M5), changed-file discovery with GitHub annotations and job-summary output (M6), and the single in-place PR comment (M7) exist; the GitHub Action and copy-in script are still being built, milestone by milestone, from the MVP issue list. Features below are the MVP scope, not current behaviour.

---

## 🚀 Why usd-asset-gate Exists

USD assets live in git next to code, but they rarely get the checks code gets.  
A missing reference, a stage with no default prim, an asset authored in the wrong units, or a "small prop" with half a million prims all sail through review because nobody opens the file.

OpenUSD already has the validators — `usdchecker` and the validation framework behind it — but every team has to wire them into CI by hand, and the PyPI `usd-core` wheel does not even include the `usdchecker` executable.

With usd-asset-gate:
- One YAML file sets your rules: units, up axis, budgets, naming, file sizes.
- Only the files a pull request changed are checked.
- Problems show up as annotations on the diff and as **one** PR comment that is updated in place, never spammed.
- The same command runs on your laptop before you push.

---

## 🌍 A Day in the Life with usd-asset-gate

You push a branch that updates a prop.  
Within a minute:
1. **The check goes red** because `crate.usda` references `textures/crate_base.usda`, which you forgot to commit.  
2. **An annotation on the file** says exactly which prim holds the broken reference.  
3. **The PR comment** also notes the asset is at 12,000 prims against a 5,000-prim budget.  
4. **You fix both, push again**, and the same comment flips to "all checks pass" — no pile of bot messages.

---

## 🎯 Purpose

usd-asset-gate turns OpenUSD validation into a pull-request gate with project-specific policy.

Core responsibilities:
- Running the upstream OpenUSD validators (`pxr.UsdValidation`, the framework `usdchecker` uses).
- Enforcing configurable house rules from `.usd-asset-gate.yml`.
- Scoping checks to the files a change touched.
- Reporting as text, JSON, GitHub annotations, a job summary, and a single PR comment.
- Running in locked-down CI with only `pip install usd-core` and plain `git`.

---

## 📦 Features (MVP Scope)

The MVP is defined in [`MVP.md`](MVP.md) and tracked as issues labelled `mvp`.

**Upstream checks**
- All validators registered in the installed `usd-core` (28 in 26.8), selectable by name or keyword.
- Composition errors, unresolvable references and sublayers, missing default prim, missing `upAxis` / `metersPerUnit`, usdz packaging rules, shading and geometry rules.

**House rules**
- Required `metersPerUnit` and `upAxis` values.
- Default prim present, optionally of a given `kind`.
- File-size limits per extension.
- Prim-count and instancing budgets.
- Prim naming conventions by regex.

**CI integration**
- Changed-files mode (`--changed-since <ref>`).
- GitHub annotations and job summary.
- One PR comment, created once and edited in place (skipped on PRs from forks, where the log and job summary still show the results).
- Composite action, or a copy-in script with **no third-party `uses:`**.

**Planned after the MVP**
- Publishing to PyPI (`usd-asset-gate`) and listing the action on the GitHub Marketplace.
- Thumbnails of changed assets in the PR comment, rendered by `usdrecord` in an optional full-OpenUSD Docker image.
- NVIDIA `usd-validation-nvidia` as an optional extra rule source (`pip install usd-asset-gate[nvidia]`).

> For the full design, see [`ARCHITECTURE.md`](ARCHITECTURE.md). For the problem, audience and prior art, see [`CONCEPT.md`](CONCEPT.md).

---

## 🛠 Tech Stack

- Python 3.10+
- [`usd-core`](https://pypi.org/project/usd-core/) (OpenUSD Python bindings; 26.8 targeted)
- PyYAML (configuration)
- Python standard library for everything else (GitHub API calls included)
- pytest (tests)
- Bash + GitHub Actions composite action (CI packaging)

---

## 📂 Project Structure

Planned layout:

usd-asset-gate/  
 ├── usd_asset_gate/          # Python package  
 │    ├── cli.py              # check / rules / comment subcommands  
 │    ├── model.py            # Finding, Severity, Report  
 │    ├── runner.py           # runs rules over files, collects findings  
 │    ├── stage.py            # opens each stage once, captures diagnostics  
 │    ├── config.py           # .usd-asset-gate.yml loader and schema  
 │    ├── discovery.py        # paths, globs, changed-since-ref  
 │    ├── rules/              # upstream adapter (upstream.py) and house rules  
 │    └── report/             # text, JSON, annotations, summary, PR comment  
 ├── schema/                 # JSON Schema for .usd-asset-gate.yml  
 ├── ci/usd-asset-gate.sh     # copy-in script for local-only CI  
 ├── action.yml               # composite GitHub Action  
 ├── tests/fixtures/          # USD files that must pass or fail  
 ├── CONCEPT.md               # Problem, audience, non-goals, prior art  
 ├── MVP.md                   # MVP scope, acceptance criteria, milestones  
 ├── ARCHITECTURE.md          # Components, rule engine, config, outputs  
 └── README.md                # This file  

---

## 🚀 Getting Started

Not available yet. The intended usage is:

```bash
pip install usd-core usd-asset-gate
usd-asset-gate check assets/
usd-asset-gate check --changed-since origin/main --format github
```

---

## 📄 License

This project is licensed under the MIT License — see [`LICENSE`](LICENSE).  

**Copyright © 2026 Daniel McCoy Stephenson.**

---

## 📬 Contact

For inquiries, feature requests, or contributions, please open an issue in the GitHub repository:  
https://github.com/Preponderous-Software/usd-asset-gate
