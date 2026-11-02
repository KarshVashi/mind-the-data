# ADR-002: Polling interval

Date: 2026-11-02
Status: accepted (revisit after the week-one data review)

## Context

TfL has no feed of actual train arrivals. The measured view of the league infers arrivals from successive `StopPoint/{id}/Arrivals` boards. A train counts as arrived when it is on the board close to the platform in one poll and gone in the next.

How often we poll sets the cost and the shape of the collector. The question is how accurate an inferred arrival time is at a given polling interval.

## Decision

Poll every feed every 60 seconds, including arrivals, from a Lambda run each minute by EventBridge Scheduler.

The inferred arrival time is the last `expectedArrival` TfL gave for that train, not the time of the poll. Most of the arrival's precision therefore comes from TfL's own prediction, not from how often we poll.

## Alternatives considered

- **30-second loop on an always-on Fargate Spot task.** This halves the time a train can leave the board unseen. It costs about £3 to £4 a month more and adds a long-running service to operate.
- **A 30-second Lambda pattern (two staggered schedules, or a sleep inside one invocation).** Two staggered schedules make overlapping polls and the poll budget harder to reason about. A sleep inside one invocation pays for idle Lambda time.

## Consequences

- **Cost:** the collector fits inside Lambda's always-free allowance.
- **Precision:** the inferred arrival time is only as good as TfL's final prediction for that train.
- **Revisit trigger:** the week-one data-quality review (M2) measures how far `expectedArrival` moves in a train's final minute.
  - If the error exceeds ±45 seconds for most lines, arrivals move to a 30-second loop on Fargate Spot, recorded in a new ADR.
  - Line status and disruptions stay at 60 seconds either way.
