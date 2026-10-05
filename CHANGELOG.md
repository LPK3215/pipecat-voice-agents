# Changelog (repository level)

This file records changes at the **repository** level: root documentation and policy files, and the
addition, removal or repositioning of subprojects.

Version changes **inside** each project are recorded in that project's own `CHANGELOG.md`:

- [`pipecat-quickstart/CHANGELOG.md`](pipecat-quickstart/CHANGELOG.md)
- [`voice-module-dify/CHANGELOG.md`](voice-module-dify/CHANGELOG.md)
- [`pipecat-open-webui/voice-docs/CHANGELOG.md`](pipecat-open-webui/voice-docs/CHANGELOG.md)

Follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **GitHub is now the primary repository**: <https://github.com/LPK3215/pipecat-voice-agents>. The CNB
  remote stays as a secondary mirror (remote name `cnb`). Every piece of repository identity — repository
  URL, author name, noreply e-mail, copyright and citation — was moved from CNB to GitHub across
  **27 files** in one pass.
- **GitHub Pages deployment**: the overview site is published from `main` / `/docs` in **classic mode**
  (no Actions) at <https://lpk3215.github.io/pipecat-voice-agents/>, and that URL is filled into the
  repository homepage. `docs/.nojekyll` disables Jekyll; all assets use relative paths.
- **Community files**: [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/) (bug report, feature request,
  config) and [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md). Both forms ask which
  project is affected first (this is a multi-project repository), and the PR checklist mirrors what
  [`CONTRIBUTING.md`](CONTRIBUTING.md) already states.
- **Document map**: a "Where to go next / 接下来去哪" table in both READMEs routes every question to the
  document that owns it, instead of repeating the detail at the root.
- **Project-scoped labels** for issue triage: `pipecat-quickstart`, `voice-module-dify`,
  `pipecat-open-webui` (colours match the project emoji in the READMEs).
- [`SECURITY.md`](SECURITY.md): how to report a vulnerability and what is in scope. Reports go through
  GitHub's private vulnerability reporting, so no personal address has to be published.

- **Bilingual documentation**: [`README.zh-CN.md`](README.zh-CN.md) added as the Chinese companion to the
  English primary [`README.md`](README.md). Both keep an identical section structure and link to each other
  at the top.
- **Two-layer language policy**: root documents (`CONTRIBUTING.md`, `FAQ.md`, `CHANGELOG.md`, `AUTHORS`) are
  English-only; the asset layer (diagrams, charts, banners, the project overview page and the card) is
  produced **once, in English**, with no language suffix, and is referenced by both README versions at the
  same relative path.
- Repository relationship diagram [`docs/repo-map.svg`](docs/repo-map.svg) and its generator
  [`scripts/visualization/generate_repo_map.mjs`](scripts/visualization/generate_repo_map.mjs)
  (project versions are read from each project's own source of truth at run time, never hard-coded).
- Project overview page: [`project_overview.html`](project_overview.html) (root entry) plus
  [`project_overview/`](project_overview/) (`index.html` / `style.css` / `script.js` / `charts.js`)
  — a repository-view tour of the three projects with cross-project benchmark charts, plus
  [`project_card.html`](project_overview/project_card.html), a condensed one-pager that exports to PNG.
- GitHub Pages deployment copy: [`docs/`](docs/) (classic mode, with `docs/.nojekyll`). Document links in the
  deployed copy point at the repository's web address, because the site itself does not contain the repository
  files and relative paths would 404.
- Repository layout: [`research/`](research/) directory; `pipecat-项目调研.md` moved there from the
  repository root, so the root no longer mixes "content documents" with "policy files"
  (`LICENSE` / `CONTRIBUTING` / `CHANGELOG` / `FAQ` / `AUTHORS`).

### Changed

- **The three project sections now follow one template** in both READMEs: intro table (identical row order,
  project-specific rows last) → what the project deliberately owns (each ending in a pointer) → companion
  documents → quick start. Missing rows were filled in: `pipecat-open-webui` had no key count,
  `voice-module-dify` had no access address.
- **Root documentation no longer restates project detail**: the pitfalls section now points at
  [`FAQ.md`](FAQ.md) (Q6 / Q2 / Q5), and the "companion documents" tables say explicitly that the detail
  lives in the linked file.
- **License badges clarified**: "MIT (root + 2 of 3 projects)" and "Open WebUI (pipecat-open-webui)" — the
  old numbering (`projects 1-2`, `project 3`) did not match the ① ② ③ numbering used in the prose.
  `pipecat-open-webui/` now also states that the upstream branding is preserved, as its license requires.

- Repository renamed: `pipecat-ai-test` → **`pipecat-voice-agents`**. The name is now content-descriptive
  and no longer reads like a test repository. Every in-repository reference (27 files, including the
  `docs/` deployment copy, the project overview page, the card and the diagram generator) was updated in
  one pass.
- Both READMEs now carry a **non-affiliation notice**: Pipecat is an open-source framework by
  **Daily / pipecat-ai**; this repository is an independent study and application of it.
- Root [`README.md`](README.md) restructured: **the three projects come first**
  (at a glance → how they differ → one section each), with repository structure, language conventions and
  license moved after them. The two duplicated opening tables were merged and the ASCII comparison table
  became a Markdown table. No content was lost.

### Removed

- **CNB cloud-environment references**: the `*.cnb.run` demo address and the `$CNB_VSCODE_PROXY_URI`
  instructions are gone. Public-access documentation is now vendor-neutral (open the port on a cloud host /
  use the environment's port-forwarding panel / reverse-proxy with Nginx). Two `cnb` matches remain and are
  unrelated: base64 integrity hashes in `package-lock.json` and a byte sequence inside `welcome.mp4`.

### Notes

- **Repository settings** (via `gh repo edit`): 8 topics; issues and discussions enabled, wiki and projects
  disabled (the repository already has `docs/` plus per-project documentation, so a wiki would be a second
  source of truth); merge policy is **squash only** with delete-branch-on-merge and auto-merge enabled;
  secret scanning and push protection enabled.
- **`docs/` and `project_overview/` are two variants, not a source and a copy**: `docs/index.html` links to
  the repository with absolute URLs (web), `project_overview/index.html` links with `../` paths (local
  double-click). The other four assets are byte-identical. Syncing one over the other without rewriting
  those links breaks whichever side you overwrite.

## [0.1.0] - 2026-10-06

First time repository-level documentation and open-source policy files were established as a set.

### Added

- **Root license** [`LICENSE`](LICENSE): repository-owned content is MIT, with a "scope" table stating that
  `pipecat-open-webui/` is dual-licensed and **not** covered by the root MIT grant.
- [`CONTRIBUTING.md`](CONTRIBUTING.md), [`CHANGELOG.md`](CHANGELOG.md), [`FAQ.md`](FAQ.md),
  [`AUTHORS`](AUTHORS): repository-level contributing / changelog / FAQ / authors files.
- [`.gitattributes`](.gitattributes): repository-level line-ending and binary rules (each project has its own).
- Root [`README.md`](README.md): added repository structure and license sections.

### Notes

- This entry describes **repository-root** changes only; changes inside the three projects are recorded in
  their own `CHANGELOG.md` files.
