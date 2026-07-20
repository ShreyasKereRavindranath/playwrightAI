# PlaySight — Multi-Provider LLM Layer

PlaySight's AI features (self-healing locators, NL→test generation, test-repair,
quality audit, run summaries, synthetic data) are **not tied to any single LLM
vendor**. Every LLM call goes through one provider-neutral abstraction; the rest
of the app never imports a provider SDK.

Supported providers: **OpenAI**, **Anthropic Claude**, **Google Gemini**,
**Ollama** (local), **LM Studio** (local), and **any OpenAI-compatible endpoint**.

---

## Quick start

```bash
# See providers + live status
python tools/llm_config.py list

# Switch provider (remembered in config/llm_selection.json)
python tools/llm_config.py select anthropic
python tools/llm_config.py status

# …or from the runner UI:  python tools/studio.py serve → "AI Provider" tab
```

Set the selected provider's credentials in `config/.env` (see
[`config/.env.example`](config/.env.example)). **Local providers need no key.**

---

## Architecture

Clean Architecture — dependencies point inward; `llm/core` knows no SDK.

```
Interface/adapters →  utils/llm_client.LLMClient (compat shim) · UI selector · CLI
Application        →  LLMService (facade) · Registry · Factory · ConfigManager · policies
Core/domain        →  LLMProvider(Port) · LLMRequest/Response · Capability · errors
Infrastructure     →  providers/* (SDKs here only) · local/* bootstrappers
```

- **The whole app talks only to `LLMService`** (or the legacy `LLMClient` shim).
- **Provider SDKs are imported lazily**, only inside their provider module — a
  missing SDK just marks that provider unavailable; nothing crashes.
- **Capability negotiation**: `LLMService` strips request fields the selected
  provider doesn't support (e.g. `temperature` for current Claude models) instead
  of failing — that's how unsupported features are *gracefully disabled*.

### Patterns

| Pattern | Where |
|---|---|
| Common interface (Port) | `llm/core/interfaces.py::LLMProvider` |
| Provider registry + auto-discovery | `llm/registry.py` (`@register_provider`) |
| Factory | `llm/factory.py::ProviderFactory` |
| Strategy | retry/JSON-mode/auth strategies per provider |
| Dependency injection | `LLMService(config=…)`, `LLMClient(service=…)`, `BaseAgent(llm=…)` |
| Facade | `llm/service.py::LLMService` |

---

## Capability matrix

| Capability | OpenAI | Anthropic | Gemini | Ollama | LM Studio | OpenAI-compat |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| chat · streaming · system prompt | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| temperature | ✅ | ❌¹ | ✅ | ✅ | ✅ | ✅ |
| JSON / structured output | ✅ | ✅² | ✅ | ✅ | ✅ | ⚠️ |
| tools · vision · reasoning | ✅ | ✅ | ✅ | model-dep³ | model-dep | ⚠️ |
| embeddings | ✅ | ❌⁴ | ✅ | ✅ | ✅ | ⚠️ |

¹ Current Claude models reject sampling params → not advertised → stripped.
² Structured output via `output_config.format`; free-form JSON via instruction.
³ Ollama advertises a conservative set; tools/vision/reasoning depend on the model.
⁴ Anthropic has no embeddings endpoint.

---

## Configuration

Resolution order per setting: **environment variable → `config/llm_providers.json` → OS keyring**.
Only the *selected* provider's config is required.

| Provider | Key / host env | Model env | Default model |
|---|---|---|---|
| `openai` | `OPENAI_API_KEY` | `OPENAI_MODEL` / `AI_MODEL` | `gpt-4o-mini` |
| `anthropic` | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` | `claude-opus-4-8` |
| `gemini` | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | `GEMINI_MODEL` | `gemini-2.5-flash` |
| `ollama` | `OLLAMA_HOST` (no key) | `OLLAMA_MODEL` | `llama3.1` |
| `lmstudio` | `LMSTUDIO_HOST` (no key) | `LMSTUDIO_MODEL` | (loaded model) |
| `openai_compatible` | `OPENAI_COMPAT_BASE_URL` (+ optional `OPENAI_COMPAT_API_KEY`) | `OPENAI_COMPAT_MODEL` | — |

- **Select** with `AI_PROVIDER=<name>`, the UI, or `python tools/llm_config.py select <name>`.
  The choice persists to `config/llm_selection.json`.
- **Secure storage:** if `keyring` is installed, keys can live in the OS keychain
  instead of `.env`.
- `config/llm_providers.json` (gitignored) can hold per-provider `model`,
  `base_url`, `timeout`, `max_retries`, `extra` (e.g. `embedding_model`).

---

## Local LLM setup

### Ollama (fully automatic)
Selecting Ollama triggers, on first use: **detect → auto-install → start service →
pull model (with progress) → connect** — no manual `ollama pull` or config. Install
uses the official source per OS (Linux `install.sh`, macOS `brew`/dmg, Windows
installer). Override the endpoint/model with `OLLAMA_HOST` / `OLLAMA_MODEL`.

### LM Studio (detect + guide)
LM Studio's server is auto-started via the `lms` CLI when present; otherwise the
UI shows the minimum steps (install app → download a model → Developer → Start
Server on :1234). PlaySight connects automatically once the server responds.

---

## Adding a new provider

Two steps — **nothing else in the app changes**:

1. Create `llm/providers/<name>_provider.py` implementing `LLMProvider`
   (`metadata`, `capabilities`, `validate_config`, `health_check`, `complete`;
   optionally `stream`, `embed`). Import the SDK **lazily** inside methods.
2. Decorate it: `@register_provider("<name>")`.

The registry auto-discovers it, the factory builds it from config, the UI/CLI
list it, and capability negotiation applies automatically. Add a defaults row in
`llm/config.py::_DEFAULTS` if it needs env-var conventions.

See `llm/providers/openai_provider.py` for the reference implementation and
`llm/providers/openai_compatible_provider.py` for a subclass example.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| AI features silently no-op | `python tools/llm_config.py status` — provider likely `needs_config` (missing key) |
| "provider not configured" at test start | Set the selected provider's key, `AI_PROVIDER=ollama` (local), or disable AI flags |
| Ollama won't start | Check `ollama serve` runs; set `OLLAMA_HOST`; see https://ollama.com/download |
| LM Studio not reachable | Start its server (Developer → Start Server, :1234); set `LMSTUDIO_HOST` |
| `temperature` seems ignored on Claude | Expected — current Claude models reject it, so the layer drops it |
| Embeddings fail on Anthropic | Anthropic has no embeddings endpoint — use another provider for embeddings |
| Provider SDK not installed | It's optional; that provider shows unavailable. `pip install -r requirements.txt` |

---

## Backward compatibility

`utils/llm_client.LLMClient` (`complete`, `complete_json`, `available`, non-raising)
is unchanged as a public surface — it now delegates to `LLMService`. Every existing
consumer (agents, self-heal, summary, judge, data-gen) works with no changes.
New code should use `from llm.service import get_service`.
