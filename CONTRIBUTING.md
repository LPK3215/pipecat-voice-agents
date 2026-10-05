# Contributing

Thanks for considering a contribution to `pipecat-voice-agents`.

> This is the **repository-level** guide and covers only what is common to this multi-project repository.
> Each project has its own `CONTRIBUTING.md` with that project's environment, checks and commit details —
> **read the one that belongs to the project you are changing**, so the same topic is not documented twice.

## Repository layout

This is a **multi-project repository (monorepo)**. The three projects are independent and can be used on
their own:

| Directory | What it is | Its own contributing guide |
|---|---|---|
| [`pipecat-quickstart/`](pipecat-quickstart/) | Full voice agent (voice is the product itself) | [`pipecat-quickstart/CONTRIBUTING.md`](pipecat-quickstart/CONTRIBUTING.md) |
| [`voice-module-dify/`](voice-module-dify/) | Voice module that **actively calls** an external platform | [`voice-module-dify/CONTRIBUTING.md`](voice-module-dify/CONTRIBUTING.md) |
| [`pipecat-open-webui/`](pipecat-open-webui/) | Voice part that is **called by** a host system | [`pipecat-open-webui/CONTRIBUTING.md`](pipecat-open-webui/CONTRIBUTING.md) |
| `research/pipecat-项目调研.md` | Framework survey (a document, not a project) | — |

## Before you commit

```bash
# Only root-level docs / policy files changed (README, FAQ, LICENSE, .gitignore, …):
# there is no build step — just make sure the links still resolve.

# Changing a project: cd into it and run that project's own checks. For example:
cd pipecat-quickstart/server && uv run pytest && uv run ruff check .
```

> **Discipline**: if you change code, **also run that project's tests and probes** (commands are in each
> project's `README.md`) and paste the measured output into the PR. This repository's rule is that
> **conclusions must not rest on "it looks right"**.

## Commit message convention (repository-wide)

Commit messages use the `type: summary` form:

| Type | Meaning |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation |
| `refactor` | Refactoring (no behaviour change) |
| `test` | Tests |
| `style` | Formatting (no logic change) |
| `chore` | Chores |

## Documentation discipline (repository-wide)

- **Write the detailed version of anything in exactly one place**; everywhere else link to it — this keeps
  multiple sources from drifting apart.
- Root-level documentation takes the **repository view**: it describes *which projects exist, how to choose
  between them, and where things live*, and does **not** restate implementation details owned by projects.
- **Language policy**:
  - **Documentation layer — two versions.** [`README.md`](README.md) is the **English primary** version;
    [`README.zh-CN.md`](README.zh-CN.md) is the Chinese companion. Both keep an identical section structure
    and link to each other at the top. All other root documents (`FAQ.md`, `CHANGELOG.md`, `AUTHORS`) are
    **English-only**.
  - **Asset layer — one copy, always English.** Diagrams, charts, banners and the project overview page are
    produced **once, in English**, with no language suffix. `README.md` and `README.zh-CN.md` reference the
    **same file at the same relative path**; a Chinese explanation goes into the Chinese prose, never into a
    second translated image.
  - Source code, commands and terminal output are quoted verbatim (plain ASCII), never paraphrased.

## License (important)

The parts of this repository are **licensed differently**. Before contributing, check which part you are
changing:

| Scope | License |
|---|---|
| Repository-owned files | **MIT** ([`LICENSE`](LICENSE)) |
| `pipecat-quickstart/`, `voice-module-dify/` | **MIT** |
| `pipecat-open-webui/` | Upstream code stays under the **Open WebUI License** (including its branding clause); files newly added on this branch are MIT |

By contributing to a project you agree that your contribution is licensed under **that project's own license**.
In particular, the **"Open WebUI" branding in `pipecat-open-webui/` must not be altered, removed, obscured or
replaced**.
