# FAQ (repository level)

> This file answers **cross-project / repository-level** questions (which project to use, how licensing works,
> why there are three). Questions about a single project's internals (how to run it, how to configure it)
> are answered in that project's own `README.md` and `FAQ.md` — for example
> [`pipecat-open-webui/FAQ.md`](pipecat-open-webui/FAQ.md).

## 1. What is the difference between the three projects? Which one should I use?

All three do "voice". They differ in two things: **who thinks**, and **where the voice layer lives**.

| I want to… | Go to |
|---|---|
| Run a working voice agent (listen → think → speak, all in my hands) | [`pipecat-quickstart/`](pipecat-quickstart/) |
| Plug voice into an existing agent system (the host is the owner) | [`pipecat-open-webui/`](pipecat-open-webui/) |
| Turn voice into a drop-in module (the module is the owner and calls an external platform) | [`voice-module-dify/`](voice-module-dify/) |

In one line: `pipecat-quickstart` is the **full stack**, `voice-module-dify` is an **add-on**
(direction: me → platform), `pipecat-open-webui` is a **symbiont** (direction: host → me). The latter two
point in **opposite directions** and **cannot replace each other**.

Full comparison: [`README.md`](README.md).

## 2. Why three separate projects instead of one?

Because they answer different questions and their **call directions are opposite**: ② actively calls a
platform, ③ is called by a host system. Merging them would force one of them to change direction and lose
its identity. They live in one repository for **comparison and study**, not to become a single product.

## 3. How many API keys do I need?

| Project | Keys needed | Notes |
|---|---|---|
| `pipecat-quickstart` | **1** | Only the LLM costs money; STT / TTS run locally |
| `voice-module-dify` | One platform key | The default case uses Dify; switching platforms means editing two `.env` lines |
| `pipecat-open-webui` | Whatever the host needs (LLM API) | UI and orchestration only; no model inference, plain CPU is enough |

## 4. How does licensing work? Can the whole repository be released under MIT?

**No — it cannot be a single blanket license.** The parts are licensed differently:

| Scope | License |
|---|---|
| Repository-owned files | **MIT** |
| `pipecat-quickstart/`, `voice-module-dify/` | **MIT** |
| `pipecat-open-webui/` | **Dual**: upstream stays under the Open WebUI License (including its branding clause); files newly added on this branch are MIT |

- The root [`LICENSE`](LICENSE) (MIT) **does not cover** `pipecat-open-webui/`.
- The **"Open WebUI" branding must not be altered, removed, obscured or replaced**.
- `pipecat-quickstart/server/bot.py` keeps the upstream `BSD 2-Clause` notice at the top of the file, which
  must be preserved as well.

See [`LICENSE`](LICENSE) and [`pipecat-open-webui/LICENSE-SUPPLEMENT.md`](pipecat-open-webui/LICENSE-SUPPLEMENT.md).

## 5. Speech is recognised as a foreign language. What now?

Whisper auto-detects the language when none is specified, and **in practice it misidentified Chinese as Thai**.
`voice-module-dify` and `pipecat-open-webui` both pin the language now (`STT_LANGUAGE=zh` /
`WHISPER_LANGUAGE=zh`) — **do not delete those lines**. See "Three pitfalls worth knowing" in
[`README.md`](README.md).

## 6. Are the latency numbers comparable across projects?

**No.** The projects measure against different baselines ("the user actually finished speaking" vs
"audio streaming ended"); the latter is roughly 0.5 s more optimistic. The numbers are only meaningful for
comparisons **within the same project / the same run**. See [`README.md`](README.md).

## 7. Is `research/pipecat-项目调研.md` a project?

No. It is a **survey of the Pipecat framework itself** (what it can do, how it is assembled, whether it fits),
answering "should I use this framework at all". The three projects are the concrete implementations.
Read it before you start.

## 8. There is no `pyproject.toml` / `requirements.txt` at the root — is that normal?

Yes. This is a **multi-project repository**; the root is not a Python package, so those language-specific
files deliberately do not exist there. Each project maintains its own (for example
`pipecat-quickstart/server/pyproject.toml`).

## 9. Why is the documentation English-first?

The repository follows a two-layer language policy:

- **Documentation layer — two versions**: [`README.md`](README.md) is the English primary version,
  [`README.zh-CN.md`](README.zh-CN.md) is the Chinese companion; all other root documents are English-only.
- **Asset layer — one copy, always English**: diagrams, charts, banners and the project overview page are
  produced once, in English, with no language suffix. Technical names and architecture terms are English
  anyway, and Chinese text inside SVGs is not guaranteed to render on GitHub.

Chinese explanations live in the Chinese prose (and in [`README.zh-CN.md`](README.zh-CN.md)), never in a
second translated image.
