"""
Single source of truth for table names used across the eval suite.

Names here match the HLD/DataFlow docs exactly. If the actual implementation
lands with different names, change them ONLY here — every test/reference
script imports from this module instead of hardcoding strings.
"""

# raw / source tables (already exist in ClickHouse today)
RAW_EVENTS = "ch_hackathon_raw_data"
CONTENT_DIM = "ch_hackathon_content_data"

# serving-layer tables the pipeline is expected to build (per HLD-sam.html / DataFlow-Sam.html / LLD-sam.md)
SESSION_STATE = "session_state"
CC_DELTA_CONTENT = "cc_delta_content"
CC_DELTA_DIMS = "cc_delta_dims"
CC_SI_MINUTE = "cc_si_minute"
SESSION_RUNS = "session_runs"          # LLD §2.7 — retraction audit ledger
PIPELINE_CURSOR = "pipeline_cursor"    # LLD §2.8 — batch progress marker
CONTENT_DIM_IMPL = "content_dim"       # LLD §2.2 — pipeline's own ReplacingMergeTree, not the raw seed table
EVENTS_RAW_IMPL = "events_raw"         # LLD §2.1 — pipeline's own copy with ingest_ts, not ch_hackathon_raw_data
