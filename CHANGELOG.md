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

- Root [`README.md`](README.md) restructured: **the three projects come first**
  (at a glance → how they differ → one section each), with repository structure, language conventions and
  license moved after them. The two duplicated opening tables were merged and the ASCII comparison table
  became a Markdown table. No content was lost.

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
