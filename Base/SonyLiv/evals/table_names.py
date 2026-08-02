"""
Single source of truth for table names used across the eval suite.

Names here match src/migrationv2/migrations/*.sql (the finalized, deployed
schema) — not the older LLD-sam.md draft. That draft's session_state/
cc_delta_dims/cc_si_minute/session_runs design was consolidated away in v2:
concurrency by dims-without-content is now just cc_delta_content summed
without grouping on content_id, session_active replaces session_state, and
there is no HLL-sketch or audit-ledger table in the deployed design. If the
implementation changes again, change names ONLY here.
"""

# raw / source tables (already exist in ClickHouse today, live in `default`
# regardless of CH_DATABASE — always fully qualified so they resolve
# whichever serving database ch_client.py is pointed at)
RAW_EVENTS = "default.ch_hackathon_raw_data"
CONTENT_DIM = "default.ch_hackathon_content_data"

# serving-layer tables per src/migrationv2/migrations/*.sql
EVENTS_INGEST = "events_ingest"                # 001 — Null engine ingestion endpoint
CONTENT_DIM_IMPL = "content_dim"               # 002 — pipeline's own ReplacingMergeTree, not the raw seed table
EVENTS_RAW_IMPL = "events_raw"                 # 003 — pipeline's own enriched copy with ingest_ts
CC_DELTA_RAW = "cc_delta_raw"                  # 004 — Null engine intermediate fold output
CC_DELTA_CONTENT = "cc_delta"                  # 005 — per-content minute deltas (ReplacingMergeTree in v4)
SESSION_ACTIVE = "session_active"              # 006 — replaces session_state; is_active flag, no fg/playing/ended
PIPELINE_CHECKPOINT = "pipeline_checkpoint"    # 007 — replaces pipeline_cursor

# Dropped in v2 (no longer built) — kept only so any straggling references
# resolve to a name that will cleanly SKIP via table_exists(), not crash.
CC_DELTA_DIMS = "cc_delta_dims"    # removed: use CC_DELTA_CONTENT summed without content_id instead
CC_SI_MINUTE = "cc_si_minute"      # removed: no HLL sketch table in v2 design
SESSION_RUNS = "session_runs"      # removed: no audit ledger table in v2 design
