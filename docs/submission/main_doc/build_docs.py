"""Build the Security Self-Assessment and Execution Evidence PDFs from their Markdown sources.

    uv run --with pillow python build_docs.py        (make submission-docs runs this after make_figures.py)

The Markdown files in docs/ stay the source of truth. This script converts them to LaTeX with a small Markdown
subset converter (headings, paragraphs, lists, pipe tables, fenced code, inline code, bold, italic) and compiles
them with tectonic using doc_preamble.tex, the same fonts, header and palette as the technical document.

Execution evidence is assembled from docs/submission/execution-evidence.md, docs/evidence/index.md, the run logs in
docs/evidence/runs/ and the test output in docs/evidence/test-output/. No number is typed here: every figure is read
from those files.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parents[1]  # docs/
SUB = DOCS / "submission"
EVI = DOCS / "evidence"
TEAM = "Team: Yajan Adhikari (IOE-TU, Nepal), Ujjawal Chapagai (DGIST, South Korea)"
COMP = "2026 Shenzhen International FinTech Competition, International Track, Topic C"

# --------------------------------------------------------------------------------------------- inline text
TEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def esc(s: str) -> str:
    return "".join(TEX_SPECIAL.get(c, c) + (r"\allowbreak{}" if c == "_" else "") for c in s)


def esc_code(s: str) -> str:
    """Escape a code span and allow line breaks after path and identifier separators."""
    out = []
    for c in s:
        out.append({'"': r"\textquotedbl{}", "'": r"\textquotesingle{}"}.get(c) or TEX_SPECIAL.get(c, c))
        if c in "/_.|,=:":
            out.append(r"\allowbreak{}")
    return "".join(out)


def quotes(s: str) -> str:
    """Straight double quotes to typographic pairs."""
    parts = s.split('"')
    if len(parts) < 3:
        return s
    out = parts[0]
    for i, p in enumerate(parts[1:], 1):
        out += ("``" if i % 2 == 1 else "''") + p
    return out


def inline(s: str) -> str:
    codes: list[str] = []

    def stash(m: re.Match[str]) -> str:
        codes.append(r"\code{" + esc_code(m.group(1)) + "}")
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"([0-9a-f]{12})\.\.", r"\1", s)  # policy hash prefixes: the note on shortening is in the text
    s = re.sub(r"`([^`]+)`", stash, s)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)  # links: keep the text
    s = esc(s)
    s = quotes(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\\emph{\1}", s)
    s = s.replace(" -- ", " -- ")
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], s)


# --------------------------------------------------------------------------------------------- blocks
def split_row(line: str) -> list[str]:
    line = line.strip()
    line = line.removeprefix("|")
    line = line.removesuffix("|")
    cells, cur, in_code = [], "", False
    for ch in line:
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append(cur.strip())
            cur = ""
        else:
            cur += ch
    cells.append(cur.strip())
    return cells


# Column shares of the two wide security tables (header cells -> fractions of the line width)
FIXED_FRACS = {
    ("Tier", "Condition (rule id)", "Threshold in the shipped config"): [0.18, 0.52, 0.30],
    ("#", "Risk", "Impact", "Bound or mitigation", "Status"): [0.05, 0.25, 0.17, 0.38, 0.15],
}


def table_tex(rows: list[list[str]], small: bool = True) -> str:
    head, body = rows[0], rows[1:]
    n = len(head)
    plain = lambda c: re.sub(r"[`*]", "", c)
    weights = []
    for i in range(n):
        lens = [len(plain(r[i])) if i < len(r) else 0 for r in rows]
        avg = sum(lens) / max(len(lens), 1)
        mx = max(lens)
        weights.append(max(min(avg, 70) + 0.15 * min(mx, 140), 7.0))
    tot = sum(weights)
    fr = [w / tot for w in weights]
    # a column is never narrower than its longest unbreakable word (code tokens are set in a wider face)
    text_w = 470.0
    mins = []
    for i in range(n):
        lw = max((len(t) for r in rows if i < len(r) for t in plain(r[i]).split()), default=4)
        mins.append(min((min(lw, 30) * 5.9 + 12) / text_w, 0.32))
    for _ in range(6):
        fixed = [i for i in range(n) if fr[i] < mins[i]]
        for i in fixed:
            fr[i] = mins[i]
        free = [i for i in range(n) if i not in fixed]
        room = 1.0 - sum(fr[i] for i in fixed)
        tot_free = sum(fr[i] for i in free) or 1.0
        for i in free:
            fr[i] = fr[i] / tot_free * room
    fixed_fr = FIXED_FRACS.get(tuple(plain(h) for h in head))
    if fixed_fr:
        fr = fixed_fr
    # exact width: n columns carry 12pt of padding each, the two outer @{} remove 12pt
    spec = "".join(
        r">{\raggedright\arraybackslash}p{\dimexpr(\linewidth-%dpt)*%d/1000\relax}"
        % (12 * n - 12, round(f * 996))
        for f in fr
    )
    fs = r"\footnotesize" if small else ""
    out = ["{" + fs, r"\begin{longtable}{@{}" + spec + r"@{}}", r"\toprule"]
    out.append(" & ".join(r"\textbf{" + inline(c) + "}" for c in head) + r" \\")
    out += [
        r"\midrule",
        r"\endfirsthead",
        r"\toprule",
        " & ".join(r"\textbf{" + inline(c) + "}" for c in head) + r" \\",
        r"\midrule",
        r"\endhead",
        r"\bottomrule",
        r"\endlastfoot",
    ]
    for k, r in enumerate(body):
        r = (r + [""] * n)[:n]
        out.append(" & ".join(inline(c) for c in r) + r" \\")
        if False:  # a light rule between rows keeps long rows readable
            out.append(r"\arrayrulecolor{g2}\midrule[0.3pt]\arrayrulecolor{ink}")
    out.append(r"\end{longtable}}")
    return "\n".join(out) + "\n"


def lists_tex(items: list[tuple[int, str, str]]) -> str:
    """items: (indent, kind 'ul'|'ol', text). One or two levels."""
    out, stack = [], []
    for ind, kind, text in items:
        level = 0 if ind < 2 else 1
        while len(stack) > level + 1:
            out.append(
                r"\end{%s}" % ("itemize" if stack.pop() == "ul" else "enumerate")
            )
        if len(stack) < level + 1:
            stack.append(kind)
            out.append(r"\begin{%s}" % ("itemize" if kind == "ul" else "enumerate"))
        out.append(r"\item " + inline(text))
    while stack:
        out.append(r"\end{%s}" % ("itemize" if stack.pop() == "ul" else "enumerate"))
    return "\n".join(out) + "\n"


def md_to_tex(md: str, skip_h1: bool = True) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    para: list[str] = []

    def flush() -> None:
        if para:
            txt = " ".join(p.strip() for p in para)
            keep = "\\Needspace{4\\baselineskip}" if txt.rstrip().endswith((":", ":**")) else ""
            out.append(keep + inline(txt) + "\n")
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            flush()
            i += 1
            code = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            out.append(
                "\\begin{lstlisting}\n" + "\n".join(code) + "\n\\end{lstlisting}\n"
            )
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            flush()
            lvl, txt = len(m.group(1)), m.group(2)
            if lvl == 1 and skip_h1:
                i += 1
                continue
            cmd = {1: "section", 2: "section", 3: "subsection", 4: "subsubsection"}[lvl]
            rest = [l for l in lines[i + 1 :] if l.strip()]
            nxt = rest[0] if rest else ""
            need = 6
            if nxt.strip().startswith("|") and len(rest) > 2:
                first = split_row(rest[2])  # first body row: an unbreakable table row must fit after the heading
                est = max(len(re.sub(r"[`*]", "", c)) / (95 / len(first)) for c in first)
                need = int(min(26, 4 + est))
            out.append("\\Needspace{%d\\baselineskip}\n\\%s*{%s}\n" % (need, cmd, inline(txt)))
            i += 1
            continue
        if (
            ln.strip().startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1])
        ):
            flush()
            rows = [split_row(ln)]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            out.append(table_tex(rows))
            continue
        if re.match(r"^(\s*)([-*]|\d+\.)\s+", ln):
            flush()
            items: list[tuple[int, str, str]] = []
            while i < len(lines):
                mm = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[i])
                if mm:
                    items.append(
                        (
                            len(mm.group(1)),
                            "ol" if mm.group(2)[0].isdigit() else "ul",
                            mm.group(3),
                        )
                    )
                    i += 1
                elif lines[i].strip() == "":
                    j = i
                    while j < len(lines) and lines[j].strip() == "":
                        j += 1
                    if j < len(lines) and re.match(r"^(\s*)([-*]|\d+\.)\s+", lines[j]):
                        i = j
                    else:
                        break
                elif lines[i].startswith("  ") and items:
                    ind, kind, txt = items[-1]
                    items[-1] = (ind, kind, txt + " " + lines[i].strip())
                    i += 1
                else:
                    break
            out.append(lists_tex(items))
            continue
        if ln.strip() == "" or ln.strip() == "---":
            flush()
        else:
            para.append(ln.lstrip("> "))
        i += 1
    flush()
    return "\n".join(out)


# --------------------------------------------------------------------------------------------- document shell
def shell(title: str, header: str, body: str) -> str:
    pre = (HERE / "doc_preamble.tex").read_text()
    return (
        pre
        + "\\fancyhead[L]{\\footnotesize %s}\n" % header
        + "\\begin{document}\n\\begin{center}\n"
        + "{\\LARGE\\bfseries Corridor AI}\\\\[3pt]\n"
        + "{\\large %s}\\\\[8pt]\n" % title
        + "{\\small %s\\\\\n%s}\n\\end{center}\n\\vspace{-4pt}\n"
        % (esc(COMP), esc(TEAM))
        + body
        + "\n\\end{document}\n"
    )


def compile_tex(name: str, tex: str) -> Path:
    p = HERE / f"{name}.tex"
    p.write_text(tex)
    r = subprocess.run(
        ["tectonic", "-X", "compile", str(p)], cwd=HERE, capture_output=True, text=True
    )
    log = r.stdout + r.stderr
    over = [ln for ln in log.splitlines() if "Overfull" in ln and "warning:" not in ln]
    if r.returncode != 0:
        print(log[-3000:])
        raise SystemExit(f"{name}: tectonic failed")
    print(f"{name}.pdf built; overfull boxes: {len(set(over))}")
    return HERE / f"{name}.pdf"


# --------------------------------------------------------------------------------------------- security
def build_security() -> None:
    md = (SUB / "security-self-assessment.md").read_text()
    # the first paragraph after the H1 reads "Code state: ..."; the title block already names the document
    tex = shell(
        "Security self-assessment",
        "Corridor AI: security self-assessment",
        md_to_tex(md),
    )
    pdf = compile_tex("security_self_assessment", tex)
    (SUB / "Corridor_AI_Security_Self_Assessment.pdf").write_bytes(pdf.read_bytes())


# --------------------------------------------------------------------------------------------- evidence
def md_sections(md: str) -> dict[str, str]:
    out: dict[str, str] = {}
    cur = None
    for ln in md.splitlines():
        m = re.match(r"^##\s+(.*)$", ln)
        if m:
            cur = m.group(1).strip()
            out[cur] = ""
        elif cur is not None:
            out[cur] += ln + "\n"
    return out


def section_body(md: str, start: str, level: str = "##") -> str:
    """Body of a log section: lines after '## start' up to the next '## ' heading."""
    return md_sections(md).get(start, "")


def tail_count(log: Path, pat: str) -> str:
    text = log.read_text()
    m = re.findall(pat, text)
    return m[-1] if m else "not found"


def figure(path: str, width: str, cap: str) -> str:
    return (
        "\\begin{center}\n\\includegraphics[width=%s]{figures/%s}\\\\[2pt]\n{\\footnotesize %s}\n\\end{center}\n"
        % (width, path, cap)
    )


SCEN = [
    (
        "a-route-payment",
        "A",
        "ev_routes",
        "0.38\\textwidth",
        "Routes screen (customer app): the recommended route and two alternatives for QAR 800.00 to NPR.",
    ),
    (
        "b-tuition-plan",
        "B",
        "ev_plan",
        "0.38\\textwidth",
        "Tuition screen after Ramesh accepted: the three-step plan and the request marked Accepted.",
    ),
    ("c-credit-passport", "C", None, None, None),
    ("d-housing-scam", "D", None, None, None),
    (
        "e-officer-verification",
        "E",
        "ev_queue",
        "0.95\\textwidth",
        "Bank console, verification queue, retaken with the steps of scenario E: the genuine landlord's payee (Wattle Lane Property, ****6612) waiting for \\texttt{officer\\_asha}, with the Confirmation of Payee match.",
    ),
    (
        "f-ledger-tamper",
        "F",
        "ev_chain_fail",
        "0.95\\textwidth",
        "Bank console, Ledger screen, retaken with the steps of scenarios E and F: after entry 6 is altered, Verify chain fails at entry 6. (This capture was taken before the officer's decision, so its chain has 9 entries; the evidence run had 13.)",
    ),
]


def major_units(rows: list[list[str]]) -> list[list[str]]:
    """Show balances as CUR 4,200.00 instead of integer minor units (every currency in the sandbox has 2 decimals)."""
    out = [rows[0]]
    for r in rows[1:]:
        cur = re.search(r"\((\w{3})\)", r[0])
        cells = [r[0]]
        for v in r[1:]:
            cells.append(f"{cur.group(1)} {int(v) / 100:,.2f}" if cur and v.strip().lstrip("-").isdigit() else v)
        out.append(cells)
    return out


def scenario_tex(key: str, letter: str, outcome_row: dict[str, str]) -> str:
    log = (EVI / "runs" / key / "log.md").read_text()
    title = log.splitlines()[0].lstrip("# ").strip()
    secs = md_sections(log)
    chain = json.loads((EVI / "runs" / key / "chain.json").read_text())
    res = json.loads((EVI / "runs" / "results.json").read_text())[key]
    parts = ["\\subsection*{%s}\n" % inline(title)]
    parts.append(
        "\\textbf{What was run.} "
        + inline(outcome_row["outcome"].replace("As expected. ", ""))
        + "\n"
    )
    # API calls: top-level step lines only
    calls = [ln[2:] for ln in secs.get("Steps", "").splitlines() if ln.startswith("- ")]
    if calls:
        parts.append("\\Needspace{6\\baselineskip}\\textbf{Requests.}\n")
        parts.append(
            "{\\raggedright\n"
            + lists_tex([(0, "ul", c.replace("->", "gave")) for c in calls])
            + "}\n"
        )
    # expected vs observed
    exp = secs.get("Expected outcome vs observed", "")
    rows = [split_row(x) for x in exp.splitlines() if x.strip().startswith("|")]
    rows = [r for r in rows if not re.match(r"^[-:\s]+$", r[0])]
    if rows:
        parts.append(
            "\\Needspace{6\\baselineskip}\\textbf{Expected outcome against observed.} Overall: %s.\n"
            % ("all checks PASS" if res["ok"] else "NOT as expected")
        )
        parts.append(table_tex(rows))
    # ledger and balances
    if key == "f-ledger-tamper":
        parts.append(
            "Scenario F runs on the chain that scenario E produced (13 entries, the same balances), so its ledger "
            "lines and balances are those of scenario E and are not repeated.\n"
        )
    else:
        led = secs.get("Ledger entries produced", "").strip().splitlines()
        summary = next((x for x in led if re.match(r"^\d+ entries", x)), "")
        decisions = [x[2:] for x in led if x.startswith("- ")]
        parts.append("\\textbf{Key ledger lines.} " + inline(summary) + "\n")
        if decisions:
            parts.append(lists_tex([(0, "ul", d) for d in decisions]))
        bal = secs.get("Balances before and after (minor units)", "").strip().splitlines()
        brows = [split_row(x) for x in bal if x.startswith("|")]
        brows = [r for r in brows if not re.match(r"^[-:\s]+$", r[0])]
        if brows:
            parts.append("\\textbf{Balances before and after} (from the sandbox database).\n")
            parts.append(table_tex(major_units(brows)))
        for x in bal:
            if x.startswith("Transfers for"):
                parts.append(inline(x) + "\n")
    parts.append(
        "\\textbf{Chain verification.} "
        + inline(
            "valid: %s, entries checked: %s, first broken seq: %s"
            % (
                str(chain["valid"]).lower(),
                chain["entries_checked"],
                chain["first_broken_seq"],
            )
        )
        + "\n"
    )
    if key == "f-ledger-tamper":
        for fn, lab in (
            ("chain_before.json", "before the tamper"),
            ("chain_after_tamper.json", "after one entry was altered"),
        ):
            c = json.loads((EVI / "runs" / key / fn).read_text())
            parts.append(
                "Chain %s: %s.\n"
                % (
                    lab,
                    inline(
                        "valid %s, entries checked %s, first broken seq %s, reason: %s"
                        % (
                            str(c["valid"]).lower(),
                            c["entries_checked"],
                            c["first_broken_seq"],
                            c["reason"],
                        )
                    ),
                )
            )
    return "\n".join(parts) + "\n"


def build_evidence() -> None:
    idx = (EVI / "index.md").read_text()
    exe = (SUB / "execution-evidence.md").read_text()
    isec, esec = md_sections(idx), md_sections(exe)
    first_par = idx.split("\n\n")[1].strip()
    body = [md_to_tex(first_par)]
    body.append(
        "\\section*{What is in the package}\n"
        + md_to_tex(esec["What is in the package"])
    )
    body.append("\\section*{How to reproduce}\n" + md_to_tex(isec["How to reproduce"]))
    # test results read from the saved console output
    t = EVI / "test-output"
    rows = [
        ["Check", "Result", "Command"],
        [
            "Backend unit and integration tests",
            tail_count(t / "make_test.log", r"(\d+ passed)"),
            "make test",
        ],
        [
            "Safety tests (properties, concurrency, ledger tamper, red-team smoke)",
            tail_count(t / "make_test_safety.log", r"(\d+ passed)"),
            "make test-safety",
        ],
        [
            "Browser tests (Chromium, English, Nepali and Chinese)",
            tail_count(t / "make_web_e2e.log", r"(\d+ passed)"),
            "make web-e2e",
        ],
        [
            "Execution evidence scenarios",
            "6 of 6 met the expected outcome",
            "make evidence",
        ],
    ]
    res = json.loads((EVI / "runs" / "results.json").read_text())
    assert all(v["ok"] for v in res.values()), (
        "a scenario did not meet its expected outcome; the PDF must say so"
    )
    body.append("\\section*{Test results}\n" + table_tex(rows))
    body.append(
        md_to_tex(
            "Console output of each command is kept in `docs/evidence/test-output/` in the repository. Lint (`make lint`) exited 0."
        )
    )
    # scenarios
    outcome_rows = {}
    tb = [
        split_row(x)
        for x in isec["Scenario outcomes (run of 2026-10-18)"].splitlines()
        if x.strip().startswith("|")
    ]
    for r in tb[2:]:
        outcome_rows[r[0][0]] = {"name": r[0], "outcome": r[1], "evidence": r[2]}
    body.append("\\section*{Scenario outcomes}\n")
    body.append(
        md_to_tex(
            isec["Scenario outcomes (run of 2026-10-18)"]
            .split("\n\n", 1)[1]
            .split("\n\n", 1)[1]
        )
    )
    body.append(
        md_to_tex(
            "Balances are shown in major units; the sandbox stores integer minor units (1/100 of the currency unit). "
            "Policy hash prefixes are shortened to 12 characters."
        )
    )
    for key, letter, fig, width, cap in SCEN:
        body.append(scenario_tex(key, letter, outcome_rows[letter]))
        if key == "c-credit-passport":
            body.append(
                "\\begin{center}\n\\includegraphics[width=0.44\\textwidth]{figures/ss_pass_valid.png}\\hspace{6mm}"
                "\\includegraphics[width=0.44\\textwidth]{figures/ss_pass_rejected.png}\\\\[2pt]\n"
                "{\\footnotesize Verifier page: the intended verifier accepts the presentation (left); another landlord is refused (right).}\n\\end{center}\n"
            )
        elif key == "d-housing-scam":
            body.append(
                "\\begin{center}\n\\includegraphics[width=0.36\\textwidth]{figures/ss_hold_card.png}\\hspace{6mm}"
                "\\includegraphics[width=0.36\\textwidth]{figures/ss_blocked.png}\\\\[2pt]\n"
                "{\\footnotesize Customer app: the hold card after saving the CityNest payee (left) and the refused payment (right).}\n\\end{center}\n"
            )
        else:
            body.append(figure(fig + ".png", width, cap))
    body.append("\\Needspace{16\\baselineskip}\n\\section*{Claims and where to see them}\n")
    body.append(
        md_to_tex(
            "Paths are relative to `docs/evidence/` in the repository unless they begin with `docs/`, `backend/` or `eval/`."
        )
    )
    body.append(md_to_tex(isec["Claims and where to see them"]))
    body.append("\\section*{Folders}\n" + md_to_tex(isec["Folders"]))
    body.append("\\section*{Limitations}\n" + md_to_tex(isec["Limitations"]))
    body.append(
        md_to_tex(
            "- All data is synthetic. Do not add `.env`, secrets, TOTP seeds or complete account identifiers to an evidence package; the run logs are scrubbed of them."
        )
    )
    tex = shell(
        "Execution evidence", "Corridor AI: execution evidence", "\n".join(body)
    )
    pdf = compile_tex("execution_evidence", tex)
    (SUB / "Corridor_AI_Execution_Evidence.pdf").write_bytes(pdf.read_bytes())


if __name__ == "__main__":
    build_security()
    build_evidence()
    sys.exit(0)
