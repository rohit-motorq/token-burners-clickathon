# CSV → StreamNative ingestion

Streams any CSV file into a StreamNative topic over the Kafka protocol. The schema is read
from the CSV header at runtime, so swapping in a different CSV needs no code change — each row
becomes one JSON message whose keys are the header columns.

## Setup

```sh
npm install
cp .env.example .env   # fill in broker, token, topic
```

Data files in this repo are Git LFS pointers; run `git lfs pull` before ingesting them.

## Run

```sh
npm run dry-run                                   # print 5 messages, no broker needed
node src/index.js                                 # ingest using .env values
node src/index.js --csv ../path/other.csv --topic public.default.other --key id
node src/index.js --limit 1000                    # smoke test against the real topic
node src/index.js --help
```

CLI flags override `.env`. See `--help` for the full list.

## Behaviour

- The file is streamed, never buffered — a 200 MB+ CSV runs in constant memory.
- Rows are produced in batches (`BATCH_SIZE`, default 5000) and each batch is awaited, so a slow
  broker applies backpressure to the file read instead of building an unbounded queue.
- `KEY_FIELD` sets the Kafka message key. Pointing it at `video_session_id` keeps every event of
  a session on one partition, which is what preserves per-session event order downstream.
- Values are produced as strings exactly as they appear in the CSV; type casting is left to the
  consumer so this stays schema-agnostic.
