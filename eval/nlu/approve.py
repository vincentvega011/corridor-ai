"""Move reviewed items from candidates.jsonl into test.jsonl.

Workflow: edit eval/nlu/candidates.jsonl by hand and set "status": "approved" on every line whose
text and gold labels you checked (fix labels first). Then run

    cd backend && uv run python ../eval/nlu/approve.py

Only lines with status "approved" are appended to test.jsonl (status dropped, "reviewed": true added);
they are marked "moved" in candidates.jsonl so they are never copied twice. test.jsonl must only ever
contain human approved items. The fine-tuning data generator drops any training example that overlaps
test.jsonl or candidates.jsonl, so the model never trains on the evaluation text.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANDIDATES = HERE / "candidates.jsonl"
TEST = HERE / "test.jsonl"


def main() -> None:
    lines = [
        json.loads(x) for x in CANDIDATES.read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    existing = (
        {json.loads(x)["id"] for x in TEST.read_text(encoding="utf-8").splitlines() if x.strip()}
        if TEST.exists()
        else set()
    )
    moved = []
    for row in lines:
        if row.get("status") == "approved" and row["id"] not in existing:
            item = {k: v for k, v in row.items() if k != "status"}
            item["reviewed"] = True
            moved.append(item)
            row["status"] = "moved"
    if moved:
        with TEST.open("a", encoding="utf-8") as f:
            for item in moved:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        CANDIDATES.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in lines) + "\n", encoding="utf-8"
        )
    pending = sum(1 for r in lines if r.get("status") == "draft")
    print(f"moved {len(moved)} approved items to test.jsonl; {pending} drafts still unreviewed")


if __name__ == "__main__":
    main()
