# Security Policy

## Supported versions

This repository is a **study repository**, not a distributed package: there are no released versions to
patch. Only the **latest commit on `main`** is maintained.

Each of the three subprojects is experimental and is documented as such
(`pipecat-open-webui/` in particular: "working end to end … experimental, not production"). Do not deploy
them as-is in a production path.

## Reporting a vulnerability

**Use GitHub's private vulnerability reporting** — repository *Security* tab → *Report a vulnerability*.
It keeps the report private until a fix is published, and it does not require publishing a personal
e-mail address.

Please include:

- which project (`pipecat-quickstart/` / `voice-module-dify/` / `pipecat-open-webui/`) — or the repository
  root, if it is about the repository itself;
- the file and, where relevant, the commit;
- how to reproduce it, with the **measured output** (this repository's rule is that conclusions must not
  rest on "it looks right").

## Scope

In scope: code and configuration that ships in this repository.

Not in scope:

- Third-party services reachable from this repository (ModelScope, Sensenova, Dify, …). Report those to
  their own vendors.
- The **upstream** portions of `pipecat-open-webui/` — that directory is a fork of Open WebUI
  (baseline `v0.11.4`); report upstream issues to the Open WebUI project.

## Secrets

API keys are the sensitive part here, and the repository is set up so they never reach it:

- `.env` files are git-ignored in every project; only `.env.example` (with placeholders) is committed.
- Secret scanning and push protection are enabled on the repository.

If you have committed a key by mistake, **rotate it at the provider first**, then clean the history.
