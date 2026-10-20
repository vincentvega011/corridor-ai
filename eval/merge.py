"""Merge a patch run into a base report: the patch's cases replace the base's cases of the same id.

    cd backend && uv run python ../eval/merge.py eval/reports/<base> eval/reports/<patch> [--modes baseline,full]

Used when only new or changed cases were re-run (P2-5). Results of the kept modes for every other case come
from the base report unchanged; modes not listed in ``--modes`` are dropped, because their results for the
changed cases would be stale (an ablation row must come from the same code as the full row). The merged
report lists both sources in ``meta`` so nothing is passed off as one run."""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import report  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("patch")
    ap.add_argument("--modes", default="baseline,full")
    ap.add_argument(
        "--keep-stale",
        action="store_true",
        help="keep base results of modes the patch did not run, marked stale",
    )
    ap.add_argument("--stamp", default="merged: patch cases replace base cases")
    args = ap.parse_args()
    base = json.loads(Path(args.base, "report.json").read_text(encoding="utf-8"))
    patch = json.loads(Path(args.patch, "report.json").read_text(encoding="utf-8"))
    keep = set(args.modes.split(","))
    patched_ids = {r["id"] for r in patch["results"]}
    patch_modes = {r["mode"] for r in patch["results"]}
    # a patch replaces a case only for the modes it actually ran
    results = [
        r for r in base["results"]
        if r["mode"] in keep and not (r["mode"] in patch_modes and r["id"] in patched_ids)
    ]  # fmt: skip
    results += [r for r in patch["results"] if r["mode"] in keep]
    if (
        args.keep_stale
    ):  # modes the patch did not run keep their old results, flagged so tables can say so
        results += [dict(r, stale=True) for r in base["results"] if r["mode"] not in keep]
    meta = dict(patch["meta"]) | {
        "modes": sorted(
            keep, key=lambda m: ("baseline", "full").index(m) if m in ("baseline", "full") else 9
        ),
        "stamp": args.stamp,
        "merged_from": {
            "base": Path(args.base).name,
            "patch": Path(args.patch).name,
            "patched_ids": sorted(patched_ids),
        },
    }
    out = report.write_report(results, meta, HERE / "reports")
    print(f"merged report: {out} ({len(results)} results, {len(patched_ids)} patched case ids)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
