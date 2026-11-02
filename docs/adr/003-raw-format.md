# ADR-003: Raw format

Date: 2026-11-02
Status: accepted

## Context

Raw TfL responses are the only irreplaceable data in the project. Every curated table and metric can be rebuilt from raw, but a missed or mangled minute is gone for good.

The raw layer must:

- **Be lossless:** keep TfL's payload exactly as received.
- **Be replayable:** curated tables can be rebuilt from it at any time.
- **Be cheap and queryable:** stored cheaply, and readable from Athena without a crawler.
- **Record failures:** a failed request must be distinguishable from a quiet period. Missing data must never read as Good Service.

## Decision

- One S3 object per feed per poll, gzipped NDJSON, at
  `raw/{feed}/dt=YYYY-MM-DD/hour=HH/{feed}_{poll_ts_utc}_{uuid}.ndjson.gz`.
  `dt` and `hour` are UTC.
- Each line is an envelope around the unchanged TfL response:

  ```json
  {"poll_id": "uuid", "polled_at": "2026-11-08T08:15:00.123Z", "feed": "arrivals",
   "request": {"path": "/StopPoint/940GZZLUOXC/Arrivals"}, "http_status": 200,
   "latency_ms": 184, "payload": [ ... TfL response unchanged ... ]}
  ```

- Failed requests (timeouts, 4xx and 5xx) are written too, with their `http_status` and an empty payload.
- Request paths are stored without the app key.
- Raw objects are kept indefinitely. They move to S3 Intelligent-Tiering after 30 days. Bucket versioning is off, because objects are write-once.
- Glue external tables read `payload` as a JSON string, with partition projection on `dt` and `hour`.

## Alternatives considered

- **Parquet at ingest.** This needs a schema up front, and a schema can drop fields TfL adds later. JSON keeps the full payload.
- **Firehose delivery.** This adds cost and buffering. It also writes objects whose boundaries don't match polls, so failed polls cannot be recorded.
- **One object per hour (appending).** S3 objects cannot be appended to, so this would need a buffer outside S3, and a crash would lose the buffered minutes.
- **DynamoDB.** Queries would need an export step, and storage costs more at this volume.

## Consequences

- **Small files:** about 140,000 objects a month means about £0.60 a month in PUT requests, and raw queries scan many small files. Staging models read one day at a time, and nothing downstream queries raw directly.
- **Schema changes:** if TfL changes a payload shape, parsing fails loudly in Pydantic and in staging, while raw still holds the data unchanged. Fixing the parser and replaying recovers it.
- **Completeness:** completeness can be measured directly from envelopes, by counting the minutes with a successful poll per feed.
- **Revisit:** if raw storage or scan cost becomes significant. The likely change is a daily compaction of raw into Parquet alongside the NDJSON, not a replacement.
