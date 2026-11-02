# ADR-002: Polling interval

Date: 2026-11-02
Status: accepted (revisit after the week-one data review)

## Context

TfL has no feed of actual train arrivals. The measured view of the league infers them from successive `StopPoint/{id}/Arrivals` boards, using this rule:

- **Arrival:** a train has arrived when it is on the board in poll *t* with `time_to_station` ≤ 120 seconds and absent in poll *t+1*, or when it shows `time_to_station` = 0.
- **Arrival time:** the last `expected_arrival` observed for that train.
- **Phantoms:** trains seen in only one poll with `time_to_station` > 120 are discarded.

How often we poll sets the cost and the shape of the collector. The question is how accurate an inferred arrival time is at a given interval.

## Decision

The collector polls each feed at these intervals:

| Feed | Interval |
| --- | --- |
| Line status, line disruption, arrivals | every 60 seconds, from a Lambda run each minute by EventBridge Scheduler |
| Stop disruption | every 5 minutes |
| Reference feeds (route sequences, timetables, severity meta) | weekly, from a separate Lambda |

The inferred arrival time comes from TfL's `expectedArrival`, not from the time of the poll. Most of the arrival's precision therefore comes from TfL's own prediction, not from how often we poll.

Polls never overlap:

- **Time budget:** each poll stops cleanly once it passes 45 seconds.
- **One at a time:** the poller has a reserved concurrency of 1.

## Alternatives considered

- **30-second loop on an always-on Fargate Spot task.** This halves the time a train can leave the board unseen. It costs about £3 to £4 a month more and adds a long-running service to operate. EventBridge Scheduler cannot fire more often than once a minute, so any sub-minute interval needs a long-running process like this one.

## Consequences

- **Cost:** the collector fits inside Lambda's always-free allowance.
- **Precision:** the inferred arrival time is only as good as TfL's final prediction for that train.
- **Revisit trigger:** the week-one data-quality review (M2) measures how far `expectedArrival` moves in the last 120 seconds before a train leaves the board.
  - If the error exceeds ±45 seconds for most lines, arrivals move to a 30-second loop on Fargate Spot, recorded in a new ADR.
  - Line status and disruptions stay at 60 seconds either way.
