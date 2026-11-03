# ADR-007: Reference Lambda sizing

Date: 2026-11-03
Status: proposed

## Context

The plan gives both Lambdas 512 MB and a 60 s timeout. The weekly reference fetch is much bigger than a poll.

**Request count:** about 199 requests a week. That is 1 severity call, 2 route-sequence calls per line, and 2 timetable calls per configured station, one per direction. Without a direction, TfL returns a disambiguation instead of a timetable.

**Memory:** every response is held until the three raw objects are written. It was measured offline by serving the largest recorded timetable (Victoria at Oxford Circus, 335 KB) for all 162 timetable requests:

| Timetable size | Peak RSS | Time | Gzipped `ref_timetable` |
| --- | --- | --- | --- |
| 335 KB each | 340 MB | 2.5 s | 1.6 MB |
| 3× (902 KB each) | 562 MB | 7.4 s | 4.0 MB |

Lines with more branches and stations, such as the Northern and the District, will have larger timetables than the Victoria. Running out of memory would lose the whole week's reference data, because nothing is written until every response is in.

**Rate limit:** the fetch shares the 500 requests per minute key limit with the poller (52 a minute). With retries in a 429 or 5xx storm, about 199 requests at concurrency 10 could crowd out a poll.

## Decision

The reference Lambda gets:

| Setting | Value |
| --- | --- |
| Memory | 1024 MB |
| Timeout | 300 s |
| Budget (`POLL_BUDGET_SECONDS`) | 240 s |
| Concurrency (`MAX_CONCURRENCY`) | 4 |

The poller keeps 512 MB, 60 s, a 45 s budget and concurrency 10.

## Alternatives considered

- **Keep 512 MB / 60 s and write timetables in chunks** (per line). This keeps memory flat, but changes ADR-003's one object per feed per run, and adds code for a weekly job.
- **Split the reference fetch into one invocation per line.** This needs a fan-out mechanism for a task that runs once a week.

## Consequences

- **Cost:** a weekly run at 1024 MB for under a minute costs well under a penny a month.
- **Time:** at concurrency 4, the fetch takes longer, about 199 requests in 50 waves. It is still well inside 240 s at TfL's usual latency.
- **Revisit after the first real weekly run.** Check "Max Memory Used" in the run's REPORT log line. Lower the memory if it stays well under 512 MB. Change to chunked writes if it approaches 1024 MB.
