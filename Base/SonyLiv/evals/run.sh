#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

echo "== 1. Data expectations (live raw data vs EDA doc claims) =="
python3 data_expectations.py
d_status=$?

echo
echo "== 2. Schema conformance (serving tables vs LLD-sam.md §2 DDL) =="
python3 test_schema.py
s_status=$?

echo
echo "== 3. ORDER BY / engine conformance (index design vs LLD §2, ClickStack-visible) =="
python3 test_ordering_key.py
o_status=$?

echo
echo "== 4. Building independent reference concurrency (Python fold, no SQL reuse) =="
python3 reference_concurrency.py reference_deltas.csv
python3 reference_intervals.py reference_intervals.csv
python3 golden_ranges.py golden_ranges.json

echo
echo "== 5. Benchmark query tests (implementation vs reference; SKIPs if pipeline not built) =="
python3 test_benchmarks.py
b_status=$?

echo
echo "== 6. Time-range query tests (peak/avg/distinct-count per range+filter vs golden, sum-of-peaks trap) =="
python3 test_range_queries.py
r_status=$?

echo
echo "== 7. Hour-grain and day-grain rollup tests =="
python3 test_grain_rollups.py
g_status=$?

echo
echo "== 8. Ledger tests (session_runs audit trail vs reference, per-session) =="
python3 test_ledger.py
l_status=$?

echo
echo "== 9. Query performance (read_rows/read_bytes/duration via system.query_log) =="
python3 test_query_performance.py
p_status=$?

echo
echo "== 10. Edge cases (Docs/EDGE_CASES.md — flapping, terminal absorption, dedup, pinning, timeouts) =="
python3 test_edge_cases.py
e_status=$?

if [ "$d_status" -ne 0 ] || [ "$s_status" -ne 0 ] || [ "$o_status" -ne 0 ] || [ "$b_status" -ne 0 ] \
   || [ "$r_status" -ne 0 ] || [ "$g_status" -ne 0 ] || [ "$l_status" -ne 0 ] || [ "$p_status" -ne 0 ] \
   || [ "$e_status" -ne 0 ]; then
  exit 1
fi
exit 0
