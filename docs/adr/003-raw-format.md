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
- Each line is an envelope for one request, around the unchanged TfL response:

  ```json
  {"poll_id": "uuid", "polled_at": "2026-11-08T08:15:00.123Z", "feed": "arrivals",
   "request": {"path": "/StopPoint/940GZZLUOXC/Arrivals"}, "http_status": 200,
   "latency_ms": 184, "error": null, "payload": [ ... TfL response unchanged ... ]}
  ```

  - **Several requests per object.** A feed that makes several requests per poll writes one line per request in the same object. Arrivals makes one request per measurement station. All lines in one object share the poll's `poll_id` and `polled_at`.
  - **Failed requests are written too:**
    - An HTTP error (4xx or 5xx) keeps its `http_status` and an empty payload.
    - A request that got no response (timeout, connection error, or the poll's time budget running out) has `http_status: null`, and `error` names the failure type.
    - `error` is `null` on success.
  - **No app key.** Request paths are stored without the app key.
- **Raw is written first.** The raw object is written before, and regardless of, Pydantic validation. A payload that fails validation is still written unchanged, and the failure is logged. Validation never blocks or skips the raw write.
- **Retention:** raw objects are kept indefinitely. Bucket versioning is off, because objects are write-once.
- **Querying:** Glue external tables read `payload` as a JSON string, with partition projection on `dt` and `hour`.

## Alternatives considered

- **Firehose to Iceberg.** It adds cost and hides the raw payload behind a transformed table.
- **DynamoDB.**

## Consequences

- **Small files:** about 140,000 objects a month means about £0.60 a month in PUT requests, and raw queries scan many small files. Staging models read one day at a time, and nothing downstream queries raw directly.
- **Schema changes:** if TfL changes a payload shape, parsing fails loudly in Pydantic and in staging, while raw still holds the data unchanged. Fixing the parser and replaying recovers it.
- **Completeness:** completeness is measured directly from envelopes, by counting the minutes with a successful poll per feed.
