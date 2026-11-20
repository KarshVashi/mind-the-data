// Should I Run: the planning and live logic, pure and DOM-free so it can be
// tested with `node --test` (ADR-018, D76). Times are epoch milliseconds or
// London minutes of the day; nothing here reads the clock or the network.
//
// Wording rules: never tell anyone to run; give spare time at p90 and say
// "estimates"; a train missing from the boards is "not shown", never
// "cancelled"; no trains shown is never "you're fine".

const MINUTE = 60_000;
const LONDON = "Europe/London";

const londonParts = new Intl.DateTimeFormat("en-GB", {
  timeZone: LONDON,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function parts(ms) {
  const out = {};
  for (const { type, value } of londonParts.formatToParts(new Date(ms))) out[type] = value;
  return out;
}

/** London's offset from UTC at an instant, in minutes (0 or 60). */
export function londonOffsetMinutes(ms) {
  const p = parts(ms);
  const wall = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second);
  return Math.round((wall - Math.floor(ms / 1000) * 1000) / MINUTE);
}

/** London wall-clock minute of the day at an instant. */
export function londonMinute(ms) {
  const p = parts(ms);
  return +p.hour * 60 + +p.minute;
}

/** "HH:MM" in London for an instant. */
export function londonClock(ms) {
  const p = parts(ms);
  return `${p.hour}:${p.minute}`;
}

/** "HH:MM" for a minute of the day (rounded down to the minute). */
export function clock(minuteOfDay) {
  const m = ((Math.floor(minuteOfDay) % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

/** Minutes after midnight for "HH:MM", or null if it is not a time. */
export function parseClock(text) {
  const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(String(text).trim());
  return match ? +match[1] * 60 + +match[2] : null;
}

/** The instant of a London wall-clock minute on the London day of `dayMs`. */
export function londonInstant(dayMs, minuteOfDay) {
  const p = parts(dayMs);
  const wall = Date.UTC(+p.year, +p.month - 1, +p.day) + minuteOfDay * MINUTE;
  // The offset at that wall time; on a clock-change day it can differ from now's.
  const guess = wall - londonOffsetMinutes(wall) * MINUTE;
  return wall - londonOffsetMinutes(guess) * MINUTE;
}

function slotRange(slot) {
  return [parseClock(slot.start), parseClock(slot.end)];
}

/**
 * The historical plan for arriving by `arriveByMinute` (London minute of the
 * day), from the published weekday slots of one origin and destination.
 *
 * The departure slot is the one holding (arrive-by - buffer - p90 run time),
 * using that slot's own p90. To be on the platform for it, allow the slot's
 * worst wait (p90 of each day's longest gap), then the walk.
 *
 * Returns {status: "ok", ...}; {status: "not_enough_data" | "withheld",
 * slot} when the departure falls in a slot without figures; or
 * {status: "outside_slots"} when it falls outside the published windows.
 */
const MAX_RUN_MINUTES = 90;

function isPublished(slot) {
  return slot.status === "ok" && Boolean(slot.run_seconds) && Boolean(slot.wait_seconds);
}

function thin(slot) {
  return { status: slot.status === "withheld" ? "withheld" : "not_enough_data", slot };
}

export function planLeaveBy({ slots, arriveByMinute, walkMinutes, bufferMinutes }) {
  const target = arriveByMinute - bufferMinutes;
  const ordered = [...slots]
    .filter((s) => s.day_class === "weekday")
    .sort((a, b) => parseClock(b.start) - parseClock(a.start));
  for (const [i, slot] of ordered.entries()) {
    if (!isPublished(slot)) continue;
    const [start, end] = slotRange(slot);
    let departBy = target - slot.run_seconds.p90 / 60;
    if (departBy < start) continue;
    if (departBy >= end) {
      // The departure is after this slot. If the next slot follows straight
      // on, it is either without figures, or published with a p90 that put
      // its own departure earlier: then take this slot's end, the earlier
      // and safer time. A gap (10:00 to 16:00) means outside the windows.
      const next = i > 0 ? ordered[i - 1] : null;
      if (!next || parseClock(next.start) !== end) return { status: "outside_slots" };
      if (!isPublished(next)) return thin(next);
      departBy = end;
    }
    const atPlatformBy = departBy - slot.wait_seconds.worst_p90 / 60;
    return {
      status: "ok",
      slot,
      departBy,
      atPlatformBy,
      leaveBy: atPlatformBy - walkMinutes,
      typicalLeaveBy:
        arriveByMinute - bufferMinutes - slot.run_seconds.p50 / 60 - slot.wait_seconds.typical / 60 - walkMinutes,
    };
  }
  // No published slot holds the departure: a slot without figures that
  // could (it starts before the target, within a run's length of it) says so.
  const candidate = ordered.find((s) => {
    const [start, end] = slotRange(s);
    return !isPublished(s) && start < target && end > target - MAX_RUN_MINUTES;
  });
  return candidate ? thin(candidate) : { status: "outside_slots" };
}

/**
 * Which board destinations (destinationNaptanId) reach the journey's
 * destination: a Map from id to {name, isAmbiguous}, from the published
 * origin file's destination entry.
 */
export function reachFromPublished(destinationEntry) {
  const reach = new Map();
  for (const t of destinationEntry?.train_destinations ?? []) {
    reach.set(t.naptan_id, { name: t.name, isAmbiguous: Boolean(t.is_ambiguous) });
  }
  return reach;
}

/**
 * The same from a single ordered stop list (a line with one route each way):
 * a train for any stop at or after the destination reaches it.
 */
export function reachFromStops(stops, origin, destination) {
  const ids = stops.map((s) => s.naptan_id);
  const a = ids.indexOf(origin);
  const b = ids.indexOf(destination);
  const reach = new Map();
  if (a < 0 || b <= a) return reach;
  for (const stop of stops.slice(b)) reach.set(stop.naptan_id, { name: stop.name, isAmbiguous: false });
  return reach;
}

/** True on a London Monday to Friday (bank holidays are not known here). */
export function isLondonWeekday(ms) {
  const day = new Intl.DateTimeFormat("en-GB", { timeZone: LONDON, weekday: "short" }).format(new Date(ms));
  return !["Sat", "Sun"].includes(day);
}

/** The weekday planning slot holding an instant, or null (also at weekends). */
export function slotAt(slots, ms) {
  if (!isLondonWeekday(ms)) return null;
  const minute = londonMinute(ms);
  return (
    slots.find((s) => {
      const [start, end] = slotRange(s);
      return s.day_class === "weekday" && minute >= start && minute < end;
    }) ?? null
  );
}

/** Share of timetabled trains not tracked on the boards, or null. */
export function boardsMissShare(slot) {
  if (!slot || slot.status !== "ok" || !slot.scheduled_per_day || slot.trains_seen_per_day == null) return null;
  const missed = 1 - slot.trains_seen_per_day / slot.scheduled_per_day;
  return missed > 0.02 ? missed : null;
}

function spareText(minutes) {
  const m = Math.max(0, Math.floor(minutes));
  return `about ${m} min to spare (p90)`;
}

/**
 * The live verdict from one /StopPoint/{origin}/Arrivals response.
 *
 * journey: {line, origin, destination, destinationName, walkMinutes,
 * bufferMinutes}; reach: a Map from reachFrom*; runP90Seconds: the planning
 * slot's p90 run time, or null when none is published; missShare: share of
 * timetabled trains the boards miss, or null.
 */
export function assessArrivals({ arrivals, journey, reach, runP90Seconds, nowMs, arriveByMs, missShare = null }) {
  const walkMs = journey.walkMinutes * MINUTE;
  const bufferMs = journey.bufferMinutes * MINUTE;
  const byText = londonClock(arriveByMs);
  const trains = [];
  for (const a of arrivals ?? []) {
    if (a.naptanId !== journey.origin || a.lineId !== journey.line) continue;
    // On a loop a board destination can be reached either way round.
    if (journey.direction && a.direction && a.direction !== journey.direction) continue;
    const arrivalMs = Date.parse(a.expectedArrival);
    if (!Number.isFinite(arrivalMs) || arrivalMs < nowMs - MINUTE) continue;
    const destination = a.destinationNaptanId || null;
    let kind;
    if (!destination) kind = "destination_not_shown";
    else if (!reach.has(destination)) continue;
    else kind = reach.get(destination).isAmbiguous ? "may_not_call" : "reaches";
    const atDestinationMs = runP90Seconds == null ? null : arrivalMs + runP90Seconds * 1000;
    const spareMinutes = atDestinationMs == null ? null : (arriveByMs - atDestinationMs) / MINUTE;
    trains.push({
      id: a.id,
      kind,
      arrivalMs,
      arrival: londonClock(arrivalMs),
      towards: a.destinationName || a.towards || null,
      platform: a.platformName || null,
      canReach: arrivalMs - nowMs >= walkMs,
      leaveByMs: arrivalMs - walkMs,
      atDestinationMs,
      atDestination: atDestinationMs == null ? null : londonClock(atDestinationMs),
      spareMinutes,
      makesIt: spareMinutes == null ? null : spareMinutes * MINUTE >= bufferMs,
    });
  }
  trains.sort((x, y) => x.arrivalMs - y.arrivalMs);

  const missText =
    missShare == null
      ? "The boards do not list every train."
      : `About 1 in ${Math.max(2, Math.round(1 / missShare))} timetabled trains here are not tracked on the boards.`;
  const usable = trains.filter((t) => t.kind === "reaches");
  if (usable.length === 0) {
    return {
      kind: "none_shown",
      headline: `No trains to ${journey.destinationName} shown on the boards.`,
      detail: `${missText} No trains shown does not mean the line is running normally, or that it is not.`,
      trains,
    };
  }
  if (runP90Seconds == null) {
    return {
      kind: "no_run_time",
      headline: `Next train to ${journey.destinationName}: ${usable[0].arrival}.`,
      detail: "Journey times for this trip are not published yet, so the arrival time cannot be estimated.",
      trains,
    };
  }
  const reachable = usable.filter((t) => t.canReach);
  if (reachable.length === 0) {
    return {
      kind: "none_reachable",
      headline: `No train shown that you can reach in your ${journey.walkMinutes}-minute walk.`,
      detail: "The boards list roughly the next half hour; check again shortly.",
      trains,
    };
  }
  const next = reachable.find((t) => t.makesIt);
  if (!next) {
    return {
      kind: "wont_make",
      headline: `The next train you can reach (${reachable[0].arrival}) won't make ${byText} (p90).`,
      detail: "Estimates, not guarantees.",
      trains,
    };
  }
  const leaveInMinutes = (next.leaveByMs - nowMs) / MINUTE;
  if (leaveInMinutes <= 1) {
    return {
      kind: "leave_now",
      headline: `Leave now: ${spareText(next.spareMinutes)}.`,
      detail: `For the ${next.arrival} train, at ${journey.destinationName} about ${next.atDestination}. Estimates, not guarantees.`,
      trains,
      train: next,
    };
  }
  return {
    kind: "leave_by",
    headline: `Leave by ${londonClock(next.leaveByMs)}: ${spareText(next.spareMinutes)}.`,
    detail: `For the ${next.arrival} train, at ${journey.destinationName} about ${next.atDestination}. Estimates, not guarantees.`,
    trains,
    train: next,
  };
}

/** Journey <-> URL fragment ("#line=victoria&from=...&to=...&by=09:00&walk=6&buffer=2"). */
export function journeyToFragment(j) {
  const params = new URLSearchParams({
    line: j.line,
    dir: j.direction ?? "",
    from: j.origin,
    to: j.destination,
    by: j.arriveBy,
    walk: String(j.walkMinutes),
    buffer: String(j.bufferMinutes),
  });
  return `#${params.toString()}`;
}

export function journeyFromFragment(hash) {
  const params = new URLSearchParams(String(hash).replace(/^#/, ""));
  const walk = Number(params.get("walk"));
  const buffer = Number(params.get("buffer"));
  const j = {
    line: params.get("line") ?? "",
    direction: params.get("dir") || null,
    origin: params.get("from") ?? "",
    destination: params.get("to") ?? "",
    arriveBy: params.get("by") ?? "",
    walkMinutes: Number.isFinite(walk) && walk >= 0 && walk <= 120 ? walk : NaN,
    bufferMinutes: Number.isFinite(buffer) && buffer >= 0 && buffer <= 60 ? buffer : NaN,
  };
  return isValidJourney(j) ? j : null;
}

const ID = /^[0-9A-Za-z]+$/;
const DIRECTIONS = ["inbound", "outbound"];
const LINE = /^[a-z0-9-]+$/;

export function isValidJourney(j) {
  return Boolean(
    j &&
      LINE.test(j.line) &&
      ID.test(j.origin) &&
      ID.test(j.destination) &&
      j.origin !== j.destination &&
      (j.direction == null || DIRECTIONS.includes(j.direction)) &&
      parseClock(j.arriveBy) !== null &&
      Number.isFinite(j.walkMinutes) &&
      Number.isFinite(j.bufferMinutes),
  );
}
