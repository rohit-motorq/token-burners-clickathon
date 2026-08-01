#!/usr/bin/env python3
"""Turns run.sh's raw log into a markdown report: summary table up top
(pass/fail/skip per test file), full per-file output below. Parses the two
summary-line formats the suite's test files print:
  "N passed, M failed, K skipped"          (report()-based files)
  "X/Y data-expectation checks passed"     (data_expectations.py)
"""
import os
import re
import sys
import datetime

SECTION_RE = re.compile(r"^== (\d+)\. (.+?) ==$")
SUMMARY_RE = re.compile(r"^(\d+) passed, (\d+) failed, (\d+) skipped")
DATA_EXP_RE = re.compile(r"^(\d+)/(\d+) data-expectation checks passed")
WORST_RE = re.compile(r"^worst single-minute divergence: (.+)$")


def parse(log_text):
    sections = []
    cur = None
    for line in log_text.splitlines():
        m = SECTION_RE.match(line)
        if m:
            if cur:
                sections.append(cur)
            cur = {"num": m.group(1), "title": m.group(2), "lines": [], "summary": None, "extra": None}
            continue
        if cur is None:
            continue
        cur["lines"].append(line)
        m = SUMMARY_RE.match(line)
        if m:
            p, f, s = (int(x) for x in m.groups())
            cur["summary"] = {"pass": p, "fail": f, "skip": s}
        m = DATA_EXP_RE.match(line)
        if m:
            passed, total = int(m.group(1)), int(m.group(2))
            cur["summary"] = {"pass": passed, "fail": total - passed, "skip": 0}
        m = WORST_RE.match(line)
        if m:
            cur["extra"] = m.group(1)
    if cur:
        sections.append(cur)
    return sections


def main():
    if len(sys.argv) != 3:
        print("usage: gen_report.py <raw_log> <report_out.md>", file=sys.stderr)
        sys.exit(2)
    log_path, out_path = sys.argv[1], sys.argv[2]
    with open(log_path) as f:
        log_text = f.read()

    sections = parse(log_text)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ch_database = os.environ.get("CH_DATABASE", "rohitdevtesting")

    lines = []
    lines.append(f"# Eval Run Report")
    lines.append("")
    lines.append(f"- **Run at**: {now}")
    lines.append(f"- **Target database**: `{ch_database}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| # | Test file | Pass | Fail | Skip | Status |")
    lines.append("|---|---|---:|---:|---:|---|")
    total_pass = total_fail = total_skip = 0
    for s in sections:
        summ = s["summary"] or {"pass": 0, "fail": 0, "skip": 0}
        total_pass += summ["pass"]
        total_fail += summ["fail"]
        total_skip += summ["skip"]
        status = "PASS" if summ["fail"] == 0 else "FAIL"
        if summ["pass"] == 0 and summ["fail"] == 0 and summ["skip"] > 0:
            status = "SKIP"
        lines.append(f"| {s['num']} | {s['title']} | {summ['pass']} | {summ['fail']} | {summ['skip']} | {status} |")
    lines.append(f"| | **Total** | **{total_pass}** | **{total_fail}** | **{total_skip}** | "
                  f"**{'PASS' if total_fail == 0 else 'FAIL'}** |")
    lines.append("")

    notable = [s for s in sections if s["extra"]]
    if notable:
        lines.append("## Notable")
        lines.append("")
        for s in notable:
            lines.append(f"- **{s['title']}**: {s['extra']}")
        lines.append("")

    lines.append("## Full output")
    lines.append("")
    for s in sections:
        lines.append(f"### {s['num']}. {s['title']}")
        lines.append("")
        lines.append("```")
        lines.extend(s["lines"])
        lines.append("```")
        lines.append("")

    with open(out_path, "w") as f:
        f.write("\n".join(lines))
    print(f"wrote report -> {out_path}")
    sys.exit(1 if total_fail else 0)


if __name__ == "__main__":
    main()
