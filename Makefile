# Corridor AI Makefile. Targets for later phases print a notice and exit 0.
SHELL := /bin/bash
export PATH := /opt/homebrew/opt/node@22/bin:$(PATH)
BACKEND := backend
NI = @echo "not implemented yet (phase $(1))"

.PHONY: submission-docs evidence contracts-check cost-replay finetune demo-attack demo-attack-2 baseline-setup eval-quick eval-nlu gen-data gen-data-status setup seed models-up models-down models-check dev demo test test-safety eval contracts lint record-demo tamper-demo demo-reset demo-down demo-api

# Local model servers run natively on the host (Docker cannot use the Metal GPU).
PARSER_MLX ?= mlx-community/Qwen3-1.7B-4bit
# fine-tuned parser adapter (adopted in P3-1, docs/evaluation.md); set PARSER_ADAPTER= (empty) for the base model
PARSER_ADAPTER ?= models/finetune/runs/20261012T100507
LOCAL_PLANNER_MLX ?= mlx-community/Qwen3-4B-4bit
LOCAL_PLANNER ?= 0

setup: ## .env from .env.example if missing, uv sync, pnpm install, postgres up, alembic upgrade
	@[ -f .env ] || { cp .env.example .env && echo "created .env from .env.example (no API key needed for the offline demo)"; }
	cd $(BACKEND) && uv sync
	@if [ -f frontend/package.json ]; then cd frontend && pnpm install; else echo "frontend not scaffolded yet (phase P3), skipping pnpm install"; fi
	@docker info >/dev/null 2>&1 || { echo "Docker daemon is not running. Start Docker Desktop and re-run make setup."; exit 1; }
	docker compose up -d postgres
	@echo "waiting for postgres to be healthy..."
	@for i in $$(seq 1 40); do \
	  s=$$(docker inspect -f '{{.State.Health.Status}}' $$(docker compose ps -q postgres) 2>/dev/null); \
	  [ "$$s" = "healthy" ] && break; sleep 2; \
	done; [ "$$s" = "healthy" ] || { echo "postgres did not become healthy"; exit 1; }
	cd $(BACKEND) && uv run alembic upgrade head

seed: ## migrate, then reset and seed the sandbox (deterministic)
	cd $(BACKEND) && APP_ENV=dev uv run alembic upgrade head && APP_ENV=dev uv run python -m corridor.sandbox.seed

models-up: ## ollama (reader) + mlx_lm.server parser on 8081; LOCAL_PLANNER=1 also starts the local planner on 8082
	@mkdir -p logs
	@brew services start ollama >/dev/null 2>&1 || true
	@lsof -nP -iTCP:8081 -sTCP:LISTEN >/dev/null 2>&1 || \
	  (cd $(BACKEND) && nohup uv run --group local mlx_lm.server --model $(PARSER_MLX) $(if $(PARSER_ADAPTER),--adapter-path ../$(PARSER_ADAPTER)) --port 8081 \
	    > ../logs/parser.log 2>&1 < /dev/null &) ; echo "parser: $(PARSER_MLX) on :8081 (log logs/parser.log)"
	@if [ "$(LOCAL_PLANNER)" = "1" ]; then \
	  lsof -nP -iTCP:8082 -sTCP:LISTEN >/dev/null 2>&1 || \
	  (cd $(BACKEND) && nohup uv run --group local mlx_lm.server --model $(LOCAL_PLANNER_MLX) --port 8082 \
	    > ../logs/local_planner.log 2>&1 < /dev/null &) ; echo "local planner: $(LOCAL_PLANNER_MLX) on :8082"; fi
	@echo "reader: served by ollama on :11434. Run 'make models-check' to verify."

models-down: ## stop the mlx servers and ollama
	-@pkill -f "mlx_lm.server" || true
	-@brew services stop ollama >/dev/null 2>&1 || true
	@echo "local model servers stopped"

models-check: ## ping every role and print a table
	cd $(BACKEND) && uv run python -m corridor.llm.check

dev: ## API on :8000 with reload; dev chat at http://127.0.0.1:8000/dev/chat
	cd $(BACKEND) && APP_ENV=dev uv run uvicorn corridor.api.main:app --reload --host 127.0.0.1 --port 8000

demo: ## everything for both demos in one command: migrate, reset, API :8000 (demo clock 10 Oct 2026) and apps :3000, offline replay (DEMO_LLM=live for real models)
	./scripts/demo_up.sh

demo-down: ## stop what make demo started
	./scripts/demo_down.sh

demo-api: ## only the API on :8000 in the foreground, demo clock on (the old make demo)
	cd $(BACKEND) && APP_ENV=demo DEMO_CLOCK=on uv run uvicorn corridor.api.main:app --host 127.0.0.1 --port 8000

test: ## fast unit + integration
	cd $(BACKEND) && APP_ENV=test uv run pytest -q tests/unit tests/integration

finetune: ## LoRA fine-tune of the parser (about 45 min, nothing else running); logs to logs/finetune.log
	@mkdir -p logs && nohup models/finetune/train.sh > logs/finetune.log 2>&1 &

demo-attack: ## second demo: Priya, poisoned tuition invoice, baseline (left) vs Corridor AI (right)
	cd $(BACKEND) && APP_ENV=dev uv run python ../scripts/demo_attack.py

demo-attack-2: ## primary demo: Priya, first-time accommodation deposit scam, baseline (left) vs Corridor AI (right)
	cd $(BACKEND) && APP_ENV=dev uv run python ../scripts/demo_attack_2.py

cost-replay: ## ILLUSTRATIVE: seeded remittances priced with a BankWire default against the optimizer
	cd $(BACKEND) && uv run python ../scripts/cost_replay.py --json ../logs/cost_replay.json

baseline-setup: ## create, migrate and seed the separate baseline database corridor_baseline
	cd $(BACKEND) && uv run python -m corridor.baseline.setup --reset

demo-reset: ## re-seed the sandbox, clear locks, grants, TOTP counters, documents, ledger, uploads and chat state; restarts the demo API if it is running
	cd $(BACKEND) && APP_ENV=dev uv run python -m corridor.sandbox.demo_reset
	@if [ -f logs/demo_api.pid ]; then ./scripts/demo_up.sh >/dev/null && echo "demo restarted"; fi

submission-docs: ## rebuild the three submission PDFs in docs/submission (technical document, security self-assessment, execution evidence) from their sources
	$(MAKE) -C docs/submission/main_doc all

evidence: ## reproduce docs/evidence/runs: six scenarios through the real API on its own database (port 8300), replay mode, demo clock, no cloud calls
	cd $(BACKEND) && uv run python ../scripts/evidence_run.py

test-safety: ## properties + concurrency + ledger tamper + safety integration + reader, grounding and red-team smoke
	cd $(BACKEND) && APP_ENV=test uv run pytest -q tests/properties tests/concurrency tests/integration/ledger \
	  tests/integration/confirm tests/integration/executor tests/integration/agent/test_policy_wiring.py \
	  tests/integration/reader tests/integration/agent/test_documents_flow.py \
	  tests/integration/agent/test_grounding_flow.py tests/redteam_smoke \
	  tests/unit/policy tests/unit/reader tests/unit/grounding tests/unit/test_import_boundaries.py \
	  tests/integration/ablation tests/integration/baseline tests/unit/test_baseline_boundary.py tests/unit/eval

eval:
	$(call NI,P4)

contracts: ## generate frontend/lib/contracts.ts from the Python API contract (never hand edit it)
	cd $(BACKEND) && uv run python ../scripts/gen_contracts_ts.py

contracts-check: ## fail if frontend/lib/contracts.ts is out of date
	cd $(BACKEND) && uv run python ../scripts/gen_contracts_ts.py --check

lint:
	cd $(BACKEND) && uv run ruff check . && uv run ruff format --check . && uv run mypy
	@if [ -f frontend/package.json ]; then cd frontend && pnpm lint && pnpm exec tsc --noEmit; else echo "frontend not scaffolded yet (phase P3), skipping eslint and tsc"; fi

eval-nlu: ## NLU preview on eval/nlu/candidates.jsonl (draft labels) or --file test
	cd $(BACKEND) && uv run python ../eval/nlu/run.py --file candidates

gen-data: ## fine-tuning data generator (resumable; logs/finetune_gen.log)
	@mkdir -p logs
	cd $(BACKEND) && nohup uv run python ../models/finetune/gen_data.py >> ../logs/finetune_gen.log 2>&1 < /dev/null &
	@echo "started; monitor with: make gen-data-status  and  tail -f logs/finetune_gen.log"

gen-data-status:
	cd $(BACKEND) && uv run python ../models/finetune/gen_data.py --status

record-demo:
	$(call NI,P5)

tamper-demo: ## dev only: tamper with one ledger row in a scratch database and watch verification catch it
	cd $(BACKEND) && APP_ENV=dev uv run python ../scripts/tamper_demo.py

.PHONY: web-install web-build web-check web-e2e
web-install: ## install the customer app dependencies (pnpm)
	cd frontend && pnpm install --frozen-lockfile

web-build: ## production build of the customer app
	cd frontend && pnpm exec next build

web-check: ## no blocked host in the build, and a production build has no persona switcher or demo sign in
	cd frontend && node scripts/check-no-external.mjs && node scripts/check-production-build.mjs

web-e2e: ## browser tests: Chromium, production build in .next-e2e, cassette replay, own ports (API 8100, web 3100) and database: safe next to a running make demo (stop the local models first: make models-down)
	@PG=$${CORRIDOR_PG_PORT:-$$(sed -n 's/^CORRIDOR_PG_PORT=\([0-9]*\).*/\1/p' .env 2>/dev/null)}; export CORRIDOR_PG_PORT=$${PG:-5432}; \
	cd frontend && NEXT_DIST_DIR=.next-e2e NEXT_PUBLIC_DEMO_PERSONAS=1 pnpm exec next build && NEXT_DIST_DIR=.next-e2e pnpm exec playwright test
