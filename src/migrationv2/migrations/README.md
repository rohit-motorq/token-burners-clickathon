# ClickHouse Migrations — Concurrent Viewer Counting

## Execution Order
```
001_events_ingest.sql         — Null engine (ingestion endpoint)
002_content_dim.sql           — Content table + dictionary
003_events_raw.sql            — events_raw + enrichment MV
004_tables.sql                — cc_delta_content + session_active + pipeline_checkpoint
005_delta_fold.sql            — Refreshable MV (20s): emits +1/-1
006_session_active_update.sql — Refreshable MV (20s): tracks active sessions
007_checkpoint_advance.sql    — Refreshable MV (25s): moves cursor
```

## Architecture
```
INSERT → events_ingest (Null) → MV enriches → events_raw

Every 20s (delta_fold_mv):
  Read new events since checkpoint
  + Check session_active for prior state
  → Emit +1 for new activations (VideoPlay/resume/Foreground when not already active)
  → Emit -1 for VideoSessionEnd (when session was active)
  → Emit -1 for stale sessions (silent >90s, backdated)

Every 20s (session_active_update_mv):
  → Mark activated sessions is_active=1
  → Mark ended/swept sessions is_active=0
  → Update last_seen for still-active sessions

Every 25s (checkpoint_advance_mv):
  → Move cursor to max(ingest_ts)
```

## Query: Concurrency at any minute
```sql
SELECT minute, sum(delta_sessions) AS concurrent
FROM cc_delta_content
WHERE minute >= '2026-07-26 10:00:00' AND minute < '2026-07-26 11:00:00'
GROUP BY minute
ORDER BY minute;
```

## Edge Cases Handled
- Duplicate +1 prevention via is_active flag
- Pause/resume within 90s = zero delta rows (session stays active)
- Sweep emits backdated -1 at minute(last_seen + 90s)
- Session can reactivate after sweep (is_active reset to 0)
- Activate + end in same batch = net 0
- last_seen only updates on active events (not pause/background)
