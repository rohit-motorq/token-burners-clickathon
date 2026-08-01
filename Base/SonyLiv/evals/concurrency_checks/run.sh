#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

mkdir -p reports
STAMP=$(date +%Y%m%d_%H%M%S)
RAW_LOG="reports/raw_${STAMP}.log"
REPORT="reports/report_${STAMP}.md"

if [ ! -f ../reference_intervals.csv ]; then
  echo "ground truth missing, building it (python3 ../reference_intervals.py)..."
  python3 ../reference_intervals.py ../reference_intervals.csv
fi

main() {
echo "== 1. Daily peak concurrency (actual vs computed, several moments across the day) =="
python3 test_daily_peaks.py
p_status=$?

echo
echo "== 2. Time-range active-session counts (5-7min windows, actual vs computed) =="
python3 test_time_ranges.py
r_status=$?

echo
echo "== 3. Full hour 10:00:00-10:59:00 (actual vs computed, minute by minute) =="
python3 test_full_hour.py
h_status=$?

if [ "$p_status" -ne 0 ] || [ "$r_status" -ne 0 ] || [ "$h_status" -ne 0 ]; then
  return 1
fi
return 0
}

main 2>&1 | tee "$RAW_LOG"
run_status=${PIPESTATUS[0]}

python3 build_report.py "$REPORT"
report_status=$?

echo
echo "report: $REPORT"

if [ "$run_status" -ne 0 ] || [ "$report_status" -ne 0 ]; then
  exit 1
fi
exit 0
