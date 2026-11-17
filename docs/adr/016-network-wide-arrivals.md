# ADR-016: Collect arrivals for every station through the per-line endpoint

Date: 2026-11-17
Status: proposed (M8)

## Context

Arrivals are polled at 50 measurement stations (`config/stations.yaml`, ADR-002), which is enough to score a line: each line has two to five busy stations every train passes. Station-level views need every station, and the website's map shows most stations as "no data". TfL keeps no arrivals history, so station data can only build up from the day collection starts. Station disruption (closures, lift and step-free outages) is already collected network-wide every five minutes.

The 20 ranked lines serve about 430 stations. TfL offers two ways to read their boards:

- `StopPoint/{id}/Arrivals`, one call per station, as today. That is about 430 calls a minute plus the line feeds, close to the 500-a-minute limit of a registered key. It would also need far more concurrency to finish inside the 45-second poll budget (PLAN 5.3).
- `Line/{id}/Arrivals`, one call per line, returning the predictions for every stop on that line in the same Prediction shape. That is 20 calls a minute.

Volume, measured on 16 November 2026: at 08:00 the 50-station feed wrote about 51 KB gzipped a minute. The per-line responses are about 15 to 20 times larger in raw bytes, so the estimate is about 400 KB gzipped a minute at peak. That is roughly 10 GB of raw data a month, before the 30-day move to Intelligent-Tiering. A night-time probe on 17 November returned 200 for all 20 lines within 0.15 seconds each. That probe can't size daytime volume, so the first week's figures are checked against this estimate (Consequences).

## Decision

1. **A new raw feed, `line_arrivals`.** One `GET /Line/{line_id}/Arrivals` per configured line every minute, written as one object per poll with the existing envelope, failures included.
2. **Keep the 50-station `arrivals` feed and every line-level model unchanged.** The line scores keep a continuous history. The overlap at the 50 stations is the check that the per-line endpoint lists the same trains as the station boards.
3. **Timetables for every station TfL publishes them for.** The reference job now runs daily. Monday fetches the measurement stations, as before. Tuesday to Sunday each fetch a fixed sixth of every other Tube, DLR and tram station-line pair, chosen by a stable hash, so each pair is fetched once a week (about 110 to 145 calls a day). The station list comes from that day's route sequences.
   - Elizabeth line and Overground stations are left out. TfL has returned 404 for every timetable request with a 910G id since collection began.
   - Those stations get observed figures (trains seen, gaps, the gate), but no timetable-based ones (trains on the boards against the timetable, excess wait). Their measurement stations are still requested on Mondays, so the first success shows if TfL starts publishing them.
4. **Station views reuse the M4 method.** The same arrival inference, the same gate (inferred trains within 80% to 120% of the trains on the boards, ADR-014), and the same long-gap rule are applied per station, line and direction. A station that fails the gate or lacks data is published as not measured, with the reason.

## Consequences

- About 72 calls a minute (20 more), well inside the rate limit and the poll budget.
- Cost: S3 storage grows by about 10 GB a month (about $0.25 a month for each month kept). Daily staging scans about 1 GB of the new feed for its lookback window, a few cents a month. S3 writes add about $0.23 a month (one more object a minute); Lambda stays in the free tier. The total stays inside the £5 budget (H5), and `docs/cost.md` is checked against Cost Explorer after the first full month.
- Station figures start provisional: one full ISO week for a first snapshot, two for comparisons, four for trends and rankings.
- The first full week includes a check that the per-line feed lists the trains the station feed lists at the 50 stations. If it lists materially fewer, station figures stay unpublished, and this ADR is revisited (per-station polling for the stations that matter most, under the rate limit).

## Alternatives

- **Per-station polling for every station.** It reads the same boards, but it costs about 430 calls a minute, sits close to the rate limit and needs a much larger concurrency budget.
- **Replace the 50-station feed with the per-line feed.** That is cheaper, but it would change the inputs to the published line scores mid-season and remove the cross-check.
- **Sample stations in rotation.** Fewer calls, but no station would have a continuous record, so gaps between trains could not be measured.
