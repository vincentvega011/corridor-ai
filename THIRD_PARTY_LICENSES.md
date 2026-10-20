# Third-party licences

Back to the [README](README.md).

Corridor AI's own code is MIT licensed (see `LICENSE`). It depends on third-party components that keep their own licences. The licences below were read from local metadata (installed Python package metadata, `node_modules` package files, cached model cards, the local Ollama licence layer, and the font licence texts in `frontend/app/fonts/`). Where that was not possible offline, the table says "see upstream licence". The upstream licence texts are authoritative.

**All data in this repository is synthetic.** The people, accounts, businesses, documents and transactions are fictional.

### Models

| Model | Used for | Licence |
|---|---|---|
| `mlx-community/Qwen3-1.7B-4bit` (base `Qwen/Qwen3-1.7B`) | local parser (`make models-up`) | Apache-2.0 (model card) |
| `mlx-community/Qwen3-4B-4bit` (base `Qwen/Qwen3-4B`) | optional local fallback planner | Apache-2.0 (model card) |
| `qwen3-vl:4b-instruct-q4_K_M` (Ollama) | local quarantined reader | Apache-2.0 (Ollama licence layer) |
| Parser LoRA adapter (`models/finetune/`, trained by us on synthetic data) | parser fine-tune; weights are not committed, only the recipe and a 50-line sample | built on Qwen3-1.7B (Apache-2.0); our scripts are MIT |
| `deepseek-v4.1-flash:free` through the Token Harbor gateway | optional cloud planner, composer and parser fallback; not needed for the offline demo | see upstream licence |

### Python (backend)

| Package | Version | Licence |
|---|---|---|
| FastAPI, Pydantic, pydantic-settings, SQLAlchemy, Alembic | 0.143.0, 2.14.0, 2.15.0, 2.1.4, 1.20.0 | MIT |
| Starlette, Uvicorn, httpx | 1.7.0, 0.54.0, 0.28.1 | BSD-3-Clause |
| psycopg | 3.3.6 | LGPL-3.0-only |
| LangGraph and its Postgres and SQLite checkpointers | 1.2.14, 3.1.2, 3.1.1 | MIT |
| OpenAI Python SDK | 3.28.0 | Apache-2.0 |
| Google OR-Tools | 9.15 | Apache-2.0 |
| LightGBM, SHAP | 4.7.0, 0.53.0 | MIT |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| PyOTP, slowapi, PyYAML | 2.10.0, 0.1.10, 6.0.3 | MIT |
| structlog | 26.1.0 | MIT OR Apache-2.0 |
| python-multipart, tzdata | 0.0.32, 2026.5 | Apache-2.0 |
| pypdfium2 | 5.14.0 | BSD-3-Clause and Apache-2.0 (plus bundled dependency licences) |
| Pillow | 12.3.0 | MIT-CMU |
| MLX, mlx-lm (local model serving, development machine only) | 0.32.3, 0.32.0 | MIT |

### Frontend

| Package | Version | Licence |
|---|---|---|
| Next.js | 16.4.0 | MIT |
| React, React DOM | 19.3.0 | MIT |
| React Flow (`@xyflow/react`) | 12.12.0 | MIT |
| Tailwind CSS and its PostCSS plugin | 4.3.3 | MIT |
| Playwright Test | 1.64.0 | Apache-2.0 |
| TypeScript | 5.9.3 | Apache-2.0 |

### Fonts (self-hosted)

IBM Plex Sans and Mono, Noto Sans, Noto Sans Devanagari, Noto Serif Devanagari, Figtree and Fraunces (all through Fontsource, package licence OFL-1.1), and the Noto Sans SC subset: SIL Open Font License 1.1. The licence texts are in `frontend/app/fonts/`.

### Not verified offline

PostgreSQL and the Docker image used by `docker-compose.yml`, Node.js, pnpm, uv, Ollama and tectonic: see upstream licence.
