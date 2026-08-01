#!/usr/bin/env python3
"""
Runs all three checks via their collect() functions and renders a single
markdown report with real tables (not code-fenced console text) — a summary
table up top, then one detail table per check.
"""
import datetime
import sys

import test_daily_peaks
import test_time_ranges
import test_full_hour


def fmt_pct(p):
    if p == float("inf"):
        return "+inf%"
    return f"{p:+.1f}%"


def daily_peaks_table(rows):
    lines = ["| minute (UTC) | actual | computed | diff | diff% | status |",
             "|---|---:|---:|---:|---:|---|"]
    for r in rows:
        lines.append(f"| {r['minute']} | {r['actual']} | {r['computed']} | {r['diff']:+d} | "
                      f"{fmt_pct(r['diff_pct'])} | {r['status']} |")
    return lines


def time_ranges_table(rows):
    lines = ["| range | window (UTC) | span | peak actual | peak computed | avg actual | avg computed | "
             "distinct sessions (actual) | distinct users (actual) | status |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in rows:
        lines.append(f"| {r['range_name']} | {r['window']} | {r['span_min']}min | "
                      f"{r['peak_actual']} | {r['peak_computed']} | "
                      f"{r['avg_actual']} | {r['avg_computed']} | "
                      f"{r['distinct_sessions_actual']} | {r['distinct_users_actual']} | {r['status']} |")
    return lines


def full_hour_table(rows):
    lines = ["| minute (UTC) | actual | computed | diff | diff% | status |",
             "|---|---:|---:|---:|---:|---|"]
    for r in rows:
        lines.append(f"| {r['minute']} | {r['actual']} | {r['computed']} | {r['diff']:+d} | "
                      f"{fmt_pct(r['diff_pct'])} | {r['status']} |")
    return lines


def section_status(rows, meta):
    if meta.get("skip_reason"):
        return "SKIP", 0, 0, 0
    n_pass = sum(1 for r in rows if r["status"] == "PASS")
    n_fail = sum(1 for r in rows if r["status"] == "FAIL")
    return ("PASS" if n_fail == 0 else "FAIL"), n_pass, n_fail, 0


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "reports/report.md"

    peaks_rows, peaks_meta = test_daily_peaks.collect()
    ranges_rows, ranges_meta = test_time_ranges.collect()
    hour_rows, hour_meta = test_full_hour.collect()

    sections = [
        ("1", "Daily peak concurrency — actual vs computed", peaks_rows, peaks_meta, daily_peaks_table),
        ("2", "Time-range active-session counts — actual vs computed", ranges_rows, ranges_meta, time_ranges_table),
        ("3", f"Full hour {test_full_hour.WINDOW_START.strftime('%H:%M:%S')}-"
              f"{test_full_hour.WINDOW_END.strftime('%H:%M:%S')} — actual vs computed", hour_rows, hour_meta, full_hour_table),
    ]

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Concurrency Checks Report", "",
        f"- **Run at**: {now}",
        "- **Actual**: independent Python fold over raw events (`../reference_intervals.py`)",
        "- **Computed**: `SELECT ... FROM cc_delta_content` (day-seeded running sum) — the delta table is all "
        "the read path needs, regardless of the pipeline's internal architecture",
        "", "## Summary", "",
        "| # | Check | Pass | Fail | Status |", "|---|---|---:|---:|---|",
    ]
    total_pass = total_fail = 0
    for num, title, rows, meta, _ in sections:
        status, n_pass, n_fail, _ = section_status(rows, meta)
        total_pass += n_pass
        total_fail += n_fail
        lines.append(f"| {num} | {title} | {n_pass} | {n_fail} | {status} |")
    lines.append(f"| | **Total** | **{total_pass}** | **{total_fail}** | "
                  f"**{'PASS' if total_fail == 0 else 'FAIL'}** |")
    lines.append("")

    for num, title, rows, meta, table_fn in sections:
        lines.append(f"## {num}. {title}")
        lines.append("")
        if meta.get("skip_reason"):
            lines.append(f"SKIPPED: {meta['skip_reason']}")
        else:
            lines.extend(table_fn(rows))
        lines.append("")

    if hour_meta.get("worst"):
        pct, m_str, actual, comp = hour_meta["worst"]
        lines.append("## Notable")
        lines.append("")
        lines.append(f"- Worst single-minute divergence in the full-hour check: `{m_str}` "
                      f"actual={actual} computed={comp} ({pct*100:+.1f}%)")
        lines.append("")

    with open(out_path, "w") as f:
        f.write("\n".join(lines))
    print(f"wrote report -> {out_path}")
    sys.exit(1 if total_fail else 0)


if __name__ == "__main__":
    main()
