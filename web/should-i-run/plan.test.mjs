// node --test web/should-i-run/ (make web-test). Hand-computed cases; no network.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  assessArrivals,
  boardsMissShare,
  clock,
  isValidJourney,
  journeyFromFragment,
  journeyToFragment,
  londonClock,
  londonInstant,
  londonMinute,
  parseClock,
  planLeaveBy,
  reachFromPublished,
  reachFromStops,
  slotAt,
} from "./plan.mjs";
import { VICTORIA } from "./lines.mjs";

const KSX = "940GZZLUKSX";
const VIC = "940GZZLUVIC";
const BXN = "940GZZLUBXN";
const WWL = "940GZZLUWWL";

function slot(start, end, overrides = {}) {
  return {
    day_class: "weekday",
    start,
    end,
    status: "ok",
    days_counted: 20,
    passes_gate: true,
    runs: 150,
    weeks_with_runs: 4,
    run_seconds: { p50: 552, p90: 614 },
    wait_seconds: { typical: 60, worst_p90: 240 },
    trains_seen_per_day: 7.8,
    scheduled_per_day: 9,
    ...overrides,
  };
}

const THIN = { status: "not_enough_data", run_seconds: null, wait_seconds: null, trains_seen_per_day: null, scheduled_per_day: null };

test("London clock in winter and summer", () => {
  // 18 Nov 2026 is GMT; 1 Jul 2026 is BST (UTC+1).
  assert.equal(londonMinute(Date.parse("2026-11-18T08:30:00Z")), 510);
  assert.equal(londonMinute(Date.parse("2026-07-01T07:30:00Z")), 510);
  assert.equal(londonClock(Date.parse("2026-07-01T07:30:00Z")), "08:30");
  assert.equal(londonInstant(Date.parse("2026-07-01T05:00:00Z"), 540), Date.parse("2026-07-01T08:00:00Z"));
  assert.equal(londonInstant(Date.parse("2026-11-18T05:00:00Z"), 540), Date.parse("2026-11-18T09:00:00Z"));
});

test("clock text round trip", () => {
  assert.equal(parseClock("09:00"), 540);
  assert.equal(parseClock("9:00"), null);
  assert.equal(parseClock("24:00"), null);
  assert.equal(clock(531.9), "08:51");
});

test("plan: departure slot holds arrive-by minus buffer minus p90", () => {
  // Arrive by 09:00 with a 2-minute buffer: target 08:58. 08:45 slot p90
  // 614 s = 10.23 min, so depart by 08:47.77, in the 08:45 slot. Be on the
  // platform 240 s earlier, 08:43.77; walk 6 min: leave by 08:37.77.
  const slots = [slot("08:30", "08:45"), slot("08:45", "09:00")];
  const plan = planLeaveBy({ slots, arriveByMinute: 540, walkMinutes: 6, bufferMinutes: 2 });
  assert.equal(plan.status, "ok");
  assert.equal(plan.slot.start, "08:45");
  assert.ok(Math.abs(plan.departBy - (538 - 614 / 60)) < 1e-9);
  assert.ok(Math.abs(plan.leaveBy - (538 - 614 / 60 - 4 - 6)) < 1e-9);
  assert.equal(clock(plan.leaveBy), "08:37");
  // Typical: 538 - 9.2 (p50) - 1 (typical wait) - 6 = 521.8, 08:41.
  assert.equal(clock(plan.typicalLeaveBy), "08:41");
});

test("plan: a long p90 moves the departure into the earlier slot", () => {
  // Target 08:50. The 08:45 slot's p90 of 1111 s gives 08:31.48, before it
  // starts; the 08:30 slot's 614 s gives 08:39.77, inside it.
  const slots = [slot("08:30", "08:45"), slot("08:45", "09:00", { run_seconds: { p50: 549, p90: 1111 } })];
  const plan = planLeaveBy({ slots, arriveByMinute: 530, walkMinutes: 0, bufferMinutes: 0 });
  assert.equal(plan.status, "ok");
  assert.equal(plan.slot.start, "08:30");
});

test("plan: a departure in a slot without figures is not_enough_data", () => {
  const slots = [slot("08:30", "08:45"), slot("08:45", "09:00", THIN)];
  const plan = planLeaveBy({ slots, arriveByMinute: 540, walkMinutes: 6, bufferMinutes: 2 });
  assert.equal(plan.status, "not_enough_data");
  assert.equal(plan.slot.start, "08:45");
});

test("plan: outside the published windows", () => {
  const slots = [slot("08:30", "08:45")];
  assert.equal(planLeaveBy({ slots, arriveByMinute: 720, walkMinutes: 5, bufferMinutes: 0 }).status, "outside_slots");
  assert.equal(planLeaveBy({ slots, arriveByMinute: 400, walkMinutes: 5, bufferMinutes: 0 }).status, "outside_slots");
});

test("reach from the published file keeps the ambiguity flag", () => {
  const reach = reachFromPublished({
    train_destinations: [
      { naptan_id: BXN, name: "Brixton", is_ambiguous: false },
      { naptan_id: "X", name: "Somewhere", is_ambiguous: true },
    ],
  });
  assert.deepEqual([...reach.keys()], [BXN, "X"]);
  assert.equal(reach.get("X").isAmbiguous, true);
});

test("reach from stops: destinations at or after B only", () => {
  const southbound = VICTORIA.directions.inbound;
  const reach = reachFromStops(southbound, KSX, VIC);
  assert.deepEqual([...reach.keys()], [VIC, "940GZZLUPCO", "940GZZLUVXL", "940GZZLUSKW", BXN]);
  // Upstream is no journey.
  assert.equal(reachFromStops(southbound, VIC, KSX).size, 0);
});

function arrival(id, minutesAway, destination, extra = {}) {
  const now = Date.parse("2026-11-18T08:30:00Z");
  return {
    id,
    naptanId: KSX,
    lineId: "victoria",
    platformName: "Southbound - Platform 5",
    direction: "inbound",
    destinationNaptanId: destination,
    destinationName: destination === BXN ? "Brixton Underground Station" : "Elsewhere",
    expectedArrival: new Date(now + minutesAway * 60_000).toISOString(),
    ...extra,
  };
}

const NOW = Date.parse("2026-11-18T08:30:00Z");
const BY_0900 = Date.parse("2026-11-18T09:00:00Z");
const JOURNEY = {
  line: "victoria",
  origin: KSX,
  destination: VIC,
  destinationName: "Victoria",
  walkMinutes: 6,
  bufferMinutes: 2,
};
const REACH = reachFromStops(VICTORIA.directions.inbound, KSX, VIC);

test("live: leave by the time that catches the next train that makes it", () => {
  // 3 min away: too soon for a 6-minute walk. 12 min away: leave by 08:36;
  // at Victoria 08:42 + 614 s = 08:52:14, 7.77 min spare.
  const verdict = assessArrivals({
    arrivals: [arrival("1", 3, BXN), arrival("2", 12, BXN), arrival("n", 4, WWL)],
    journey: JOURNEY,
    reach: REACH,
    runP90Seconds: 614,
    nowMs: NOW,
    arriveByMs: BY_0900,
  });
  assert.equal(verdict.kind, "leave_by");
  assert.equal(verdict.headline, "Leave by 08:36: about 7 min to spare (p90).");
  assert.equal(verdict.train.atDestination, "08:52");
  // The northbound train (destination upstream) is not listed.
  assert.deepEqual(
    verdict.trains.map((t) => [t.id, t.canReach]),
    [
      ["1", false],
      ["2", true],
    ],
  );
});

test("live: leave now when the walk is all the time there is", () => {
  const verdict = assessArrivals({
    arrivals: [arrival("2", 6.5, BXN)],
    journey: JOURNEY,
    reach: REACH,
    runP90Seconds: 614,
    nowMs: NOW,
    arriveByMs: BY_0900,
  });
  assert.equal(verdict.kind, "leave_now");
  // At Victoria 08:36:30 + 10:14 = 08:46:44: 13.27 min spare.
  assert.equal(verdict.headline, "Leave now: about 13 min to spare (p90).");
  assert.doesNotMatch(verdict.headline + verdict.detail, /\brun\b/i);
});

test("live: the next reachable train won't make it", () => {
  // 25 min away: Victoria 08:55 + 10:14 = 09:05:14, after 09:00.
  const verdict = assessArrivals({
    arrivals: [arrival("2", 25, BXN)],
    journey: JOURNEY,
    reach: REACH,
    runP90Seconds: 614,
    nowMs: NOW,
    arriveByMs: BY_0900,
  });
  assert.equal(verdict.kind, "wont_make");
  assert.equal(verdict.headline, "The next train you can reach (08:55) won't make 09:00 (p90).");
});

test("live: spare under the buffer is not making it", () => {
  // 18 min away: Victoria 08:48 + 10:14 = 08:58:14, 1.77 min spare < 2.
  const verdict = assessArrivals({
    arrivals: [arrival("2", 18, BXN)],
    journey: JOURNEY,
    reach: REACH,
    runP90Seconds: 614,
    nowMs: NOW,
    arriveByMs: BY_0900,
  });
  assert.equal(verdict.kind, "wont_make");
});

test("live: no trains shown is never good news", () => {
  const verdict = assessArrivals({
    arrivals: [],
    journey: JOURNEY,
    reach: REACH,
    runP90Seconds: 614,
    nowMs: NOW,
    arriveByMs: BY_0900,
    missShare: boardsMissShare(slot("08:30", "08:45", { trains_seen_per_day: 7.7, scheduled_per_day: 9 })),
  });
  assert.equal(verdict.kind, "none_shown");
  assert.equal(verdict.headline, "No trains to Victoria shown on the boards.");
  // 1 - 7.7/9 = 0.144: about 1 in 7.
  assert.match(verdict.detail, /About 1 in 7 timetabled trains here are not tracked/);
  assert.doesNotMatch(verdict.detail, /cancel|fine|good service/i);
});

test("live: trains without a destination or on another branch are flagged, not counted", () => {
  const reach = new Map([[BXN, { name: "Brixton", isAmbiguous: true }]]);
  const verdict = assessArrivals({
    arrivals: [arrival("a", 10, BXN), arrival("b", 12, "", { destinationNaptanId: undefined })],
    journey: JOURNEY,
    reach,
    runP90Seconds: 614,
    nowMs: NOW,
    arriveByMs: BY_0900,
  });
  assert.equal(verdict.kind, "none_shown");
  assert.deepEqual(
    verdict.trains.map((t) => t.kind),
    ["may_not_call", "destination_not_shown"],
  );
});

test("live: without a published run time, no arrival estimate", () => {
  const verdict = assessArrivals({
    arrivals: [arrival("2", 12, BXN)],
    journey: JOURNEY,
    reach: REACH,
    runP90Seconds: null,
    nowMs: NOW,
    arriveByMs: BY_0900,
  });
  assert.equal(verdict.kind, "no_run_time");
  assert.equal(verdict.trains[0].makesIt, null);
});

test("fragment round trip and validation", () => {
  const j = {
    line: "victoria",
    direction: "inbound",
    origin: KSX,
    destination: VIC,
    arriveBy: "09:00",
    walkMinutes: 6,
    bufferMinutes: 2,
  };
  const hash = journeyToFragment(j);
  assert.equal(hash, `#line=victoria&dir=inbound&from=${KSX}&to=${VIC}&by=09%3A00&walk=6&buffer=2`);
  assert.deepEqual(journeyFromFragment(hash), j);
  assert.equal(journeyFromFragment("#line=victoria&from=<script>&to=x&by=09:00&walk=1&buffer=1"), null);
  assert.equal(isValidJourney({ ...j, destination: KSX }), false);
  assert.equal(journeyFromFragment("#line=victoria&from=A&to=B&by=09:00&walk=500&buffer=1"), null);
});

test("plan: never carries a departure across the gap between windows", () => {
  // Every slot published, 06:00-10:00 and 16:00-19:00. Arriving by 13:00 or
  // 16:05 means departing by 12:48 or 15:53, in neither window.
  const slots = [];
  for (const [from, to] of [
    [360, 600],
    [960, 1140],
  ]) {
    for (let m = from; m < to; m += 15) {
      slots.push(slot(clock(m), clock(m + 15), { run_seconds: { p50: 540, p90: 600 } }));
    }
  }
  for (const arriveBy of [780, 965]) {
    const plan = planLeaveBy({ slots, arriveByMinute: arriveBy, walkMinutes: 6, bufferMinutes: 2 });
    assert.equal(plan.status, "outside_slots");
  }
  // Arriving by 16:30: depart by 16:18, in the 16:15 slot.
  const plan = planLeaveBy({ slots, arriveByMinute: 990, walkMinutes: 6, bufferMinutes: 2 });
  assert.equal(plan.slot.start, "16:15");
});

test("plan: a withheld line says so", () => {
  const withheld = { ...THIN, status: "withheld" };
  const slots = [slot("08:30", "08:45", withheld), slot("08:45", "09:00", withheld)];
  const plan = planLeaveBy({ slots, arriveByMinute: 540, walkMinutes: 6, bufferMinutes: 2 });
  assert.equal(plan.status, "withheld");
});

test("live: weekend instants have no weekday slot", () => {
  const slots = [slot("08:30", "08:45")];
  assert.equal(slotAt(slots, Date.parse("2026-11-18T08:35:00Z")).start, "08:30");
  // Saturday 21 November.
  assert.equal(slotAt(slots, Date.parse("2026-11-21T08:35:00Z")), null);
});

test("live: a train going the other way round is not counted", () => {
  const verdict = assessArrivals({
    arrivals: [arrival("x", 12, BXN, { direction: "outbound" })],
    journey: { ...JOURNEY, direction: "inbound" },
    reach: REACH,
    runP90Seconds: 614,
    nowMs: NOW,
    arriveByMs: BY_0900,
  });
  assert.equal(verdict.kind, "none_shown");
  assert.equal(verdict.trains.length, 0);
});

test("fragment: only inbound or outbound directions", () => {
  const rest = `from=${KSX}&to=${VIC}&by=09:00&walk=6&buffer=2`;
  assert.equal(journeyFromFragment(`#line=victoria&dir=__proto__&${rest}`), null);
  assert.equal(journeyFromFragment(`#line=victoria&dir=outbound&${rest}`).direction, "outbound");
});
