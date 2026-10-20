<!-- Tech stack, hardware and local setup (section 7). Section numbers kept so old references still resolve. -->

## 7. Tech stack, hardware and local setup

### 7.1 Versions (pin in lockfiles)
- Python 3.12, `uv` for env and deps; FastAPI, Uvicorn, Pydantic v2, SQLAlchemy 2, Alembic, psycopg 3, LangGraph, `openai` client (for all OpenAI-compatible backends), OR-Tools, LightGBM, SHAP, `cryptography`, `pyotp`, `structlog`, `slowapi`, pytest, pytest-asyncio, Hypothesis, ruff, mypy (strict on `policy`, `taint`, `confirm`, `executor`, `ledger`).
- Node 22 LTS, pnpm; Next.js, TypeScript, Tailwind, shadcn/ui, React Flow, Recharts, next-intl, Playwright for e2e.
- Postgres 16 (Docker).
- Local models: `mlx-lm` (fine-tune + serve parser), Ollama (serve reader VL model).

### 7.2 Hardware profile: MacBook Air M4, 16 GB
- **Run models natively, not in Docker.** Docker on macOS cannot use the Metal GPU. Docker Compose runs only Postgres (and optionally the backend); `mlx_lm.server` and Ollama run on the host.
- **Memory budget** (keep total under about 11 GB while demoing): parser 4-bit (1.7B about 1.5 GB, 4B about 3 GB), reader VL 4-bit (about 3 GB), Postgres about 0.3 GB, backend about 0.5 GB, Next.js dev about 1 GB, browser. Use `next build && next start` for demos (less memory than dev).
- If memory is tight: run the 1.7B parser, load the reader on demand, close other apps.
- Fanless machine: keep fine-tune runs short, plug in power, expect throttling.

### 7.3 Environment variables (`.env.example`)
```
APP_ENV=dev                     # dev | test | demo
CORRIDOR_MODE=full              # full | baseline
CORRIDOR_PG_PORT=5432           # host port for the docker compose postgres (use 5433 if 5432 is taken)
DATABASE_URL=postgresql+psycopg://corridor:corridor@localhost:5432/corridor
# Cloud provider: Token Harbor (OpenAI compatible gateway, free tier; free routes may retain prompts,
# so only synthetic data is ever sent). Model ids come from GET /v1/models, for example name:free.
TOKENHARBOR_BASE_URL=https://tokenharbor.ai/v1
TOKENHARBOR_API_KEY=
PLANNER_MODEL=                  # model id on the planner provider (tokenharbor)
COMPOSER_MODEL=                 # model id, a different :free model to spread traffic
PARSER_FALLBACK_MODEL=          # model id, a :free model that passes JSON mode
# Alibaba Model Studio is kept but disabled in backend/config/models.yaml (paid only).
PLANNER_BASE_URL=               # full URL incl. /compatible-mode/v1, only used if alibaba is enabled
PLANNER_API_KEY=
PARSER_BASE_URL=http://localhost:8081/v1     # mlx_lm.server
PARSER_MODEL=default_model      # mlx_lm.server serves its --model under the name default_model
READER_BASE_URL=http://localhost:11434/v1    # Ollama
READER_MODEL=qwen3-vl:4b-instruct-q4_K_M   # Ollama tag (pull with: ollama pull <tag>)
LOCAL_PLANNER_BASE_URL=http://localhost:8082/v1   # mlx_lm.server, started only on request
LOCAL_PLANNER_MODEL=default_model   # same convention as PARSER_MODEL (server on :8082)
LLM_RECORD=0
LLM_REPLAY=0
LEDGER_SIGNING_KEY_PATH=./secrets/issuer_ed25519.pem
```
The cloud roles (planner, composer, parser_fallback) use the `tokenharbor` provider (`TOKENHARBOR_BASE_URL`, `TOKENHARBOR_API_KEY`); their model ids are `PLANNER_MODEL`, `COMPOSER_MODEL`, `PARSER_FALLBACK_MODEL`. `PLANNER_BASE_URL` and `PLANNER_API_KEY` are only used if the disabled `alibaba` provider is enabled. The loader reads `.env` from the repository root.
Secrets never committed. `.env`, `secrets/`, model weights and adapters are in `.gitignore`.

### 7.4 Offline and China fallback plan
1. Parser and reader are local already.
2. Planner: before travelling, run the full demo script with `LLM_RECORD=1` and commit the cassettes for the demo scenarios. At the venue, if the cloud call fails, `LLM_REPLAY=1` serves recorded planner responses for scripted demo inputs. **Be transparent about this if asked.**
3. Local planner fallback (required): a local Qwen3 model via MLX handles the planner role for simple intents (balance checks, single transfers to known payees) when the cloud is unreachable; complex plans use recorded responses. Report its task completion separately in the eval. Decision 2026-10-10: the local Qwen3 4B planner is the offline fallback for simple intents only (P1 preview: 6 of 10 happy-path tasks, slow on the fanless Air); no prompt tuning for it. All main evals use the cloud planner.
4. Bring a recorded copy of the demo on the laptop and a USB drive.

## OR-Tools (tuition funding plan, P3-1)
OR-Tools (`ortools`, CP-SAT) is a local library: the funding planner makes no network call and needs no service. Its binary wheel (about 30 MB for macOS arm64, Python 3.12) must be in the local wheel cache before the offline venue setup, together with the rest of the lockfile: run `uv sync` once with network, or `uv pip download`/`uv build` the wheels into a local index, and check offline with `uv sync --offline`. The planner is deterministic (seed 0, one worker, fixed time limit) and returns "no plan" instead of a partial plan if the solver does not prove an optimum.
