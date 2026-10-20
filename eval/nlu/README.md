# NLU evaluation sets

| File | What | Counts as a result? |
|---|---|---|
| `test.jsonl` | 107 hand checked utterances (the held-out set of P2) | yes, with the human written items |
| `synthetic.jsonl` | 100+ template made items, labels known by construction (`make_synthetic.py`) | no, report separately, never a headline |
| `human_template.csv` | the sheet the owner fills: `message, meaning, language` | input only |
| `human_review.jsonl` | the sheet converted by `import_human.py import`, labels PROPOSED by fixed rules | no, until reviewed |
| `human.jsonl` | reviewed human items (`import_human.py accept`) | yes |

Never train on any of these files: `models/finetune/gen_data.py` drops generated text that is too close to any of them.

Workflow for the human items: fill `human_template.csv` (save as `human_filled.csv`), run
`python3 eval/nlu/import_human.py import eval/nlu/human_filled.csv`, open `human_review.jsonl`, correct `intent` and `slots`
and set `"reviewed": true` per line, then `python3 eval/nlu/import_human.py accept`. Evaluate with
`cd backend && uv run python ../eval/nlu/run.py --file human` (or `--file synthetic`).
