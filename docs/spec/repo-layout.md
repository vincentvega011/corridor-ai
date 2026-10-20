<!-- Repository layout (section 9). Section numbers kept so old references still resolve. -->

## 9. Repository layout

```
corridor-ai/
├── README.md                    # judge-facing: what, why, quickstart, results summary
├── Makefile
├── docker-compose.yml           # postgres (+ optional backend)
├── .env.example
├── backend/
│   ├── pyproject.toml
│   ├── alembic/
│   ├── config/                  # models.yaml, policy.yaml, corridors.yaml
│   ├── src/corridor/
│   │   ├── api/  agent/  nlu/  reader/  taint/  policy/  confirm/
│   │   ├── executor/  ledger/  optimizer/  risk/  passport/  grounding/
│   │   ├── sandbox/  llm/  contracts/  config.py  logging.py
│   └── tests/
│       ├── unit/  integration/  properties/  concurrency/
├── frontend/
│   ├── app/(customer)/  app/console/  components/  lib/  messages/{en,ne,zh-CN}.json
│   └── public/fonts/
├── models/finetune/             # gen_data.py, train.sh, eval_parser.py, MODEL_CARD.md
├── eval/
│   ├── tasks/  redteam/  nlu/  replay/  cassettes/  reports/  run.py
├── data/
│   ├── seeds/  fx/  providers/  calendar/
├── docs/
│   ├── architecture.md  threat-model.md  policy-rules.md  assumptions.md
│   ├── security-self-assessment.md  execution-evidence/  adr/  templates/
│   └── submission/              # final PDFs
└── scripts/                     # demo_reset.sh, tamper_demo.py, record_demo.sh
```

`backend/src/corridor/contracts/` holds every shared Pydantic model (section 10). It is written in P0 and changed only deliberately, because parallel development sessions in different worktrees build against it.
