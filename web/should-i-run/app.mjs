// Should I Run: the page. All logic worth testing is in plan.mjs; this file
// reads the form, fetches, and writes text into the page (never HTML).
//
// Live times come from TfL's public arrivals API with no key: each visitor's
// browser calls it directly, inside TfL's anonymous limit. The project's key
// is never used here (ADR-018).
import {
  assessArrivals,
  boardsMissShare,
  clock,
  isValidJourney,
  journeyFromFragment,
  journeyToFragment,
  londonClock,
  londonInstant,
  parseClock,
  planLeaveBy,
  reachFromPublished,
  reachFromStops,
  slotAt,
} from "./plan.mjs";
import { BUILT_IN_LINES } from "./lines.mjs";

const DATA = "https://data.karshvashi.com";
const TFL = "https://api.tfl.gov.uk";
const REFRESH_MS = 30_000;
const STORAGE_KEY = "should-i-run.journey";

const $ = (id) => document.getElementById(id);
const form = $("journey-form");

let index = null; // journeys/index.json, once published
let journey = null;
let origin = null; // the journey's origin file, when published
let timer = null;

function el(tag, attrs = {}, text = "") {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text) node.textContent = text;
  return node;
}

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store", credentials: "omit" });
  if (!response.ok) throw new Error(`${response.status} for ${url}`);
  return response.json();
}

// ---- Stations ---------------------------------------------------------------

function lineChoices() {
  if (index) {
    const lines = [...new Set(index.origins.map((o) => o.line_id))].sort();
    return lines.map((id) => ({ id, name: id.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) }));
  }
  return BUILT_IN_LINES.map((l) => ({ id: l.line, name: l.name }));
}

function stationsFor(lineId) {
  if (index) {
    const ids = new Set();
    for (const o of index.origins) {
      if (o.line_id !== lineId) continue;
      ids.add(o.origin_naptan_id);
      for (const d of o.destination_naptan_ids) ids.add(d);
    }
    return [...ids]
      .map((id) => ({ naptan_id: id, name: index.stations[id] ?? id }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }
  const line = BUILT_IN_LINES.find((l) => l.line === lineId);
  return line ? line.directions.inbound : [];
}

/** The direction in which `to` follows `from`, or null. */
function directionFor(lineId, from, to) {
  if (index) {
    const entry = index.origins.find(
      (o) => o.line_id === lineId && o.origin_naptan_id === from && o.destination_naptan_ids.includes(to),
    );
    return entry ? entry.direction : null;
  }
  const line = BUILT_IN_LINES.find((l) => l.line === lineId);
  if (!line) return null;
  for (const [direction, stops] of Object.entries(line.directions)) {
    if (reachFromStops(stops, from, to).size > 0) return direction;
  }
  return null;
}

function fill(select, options, selected) {
  select.replaceChildren(
    ...options.map((o) => {
      const option = el("option", { value: o.value }, o.label);
      if (o.value === selected) option.selected = true;
      return option;
    }),
  );
}

function fillForm(j) {
  const lines = lineChoices();
  const lineId = j?.line ?? lines[0]?.id;
  fill($("line"), lines.map((l) => ({ value: l.id, label: l.name })), lineId);
  const stations = stationsFor(lineId).map((s) => ({ value: s.naptan_id, label: s.name }));
  fill($("origin"), stations, j?.origin ?? "940GZZLUKSX");
  fill($("destination"), stations, j?.destination ?? "940GZZLUVIC");
  if (j) {
    $("arrive-by").value = j.arriveBy;
    $("walk").value = String(j.walkMinutes);
    $("buffer").value = String(j.bufferMinutes);
  }
}

function readForm() {
  const j = {
    line: $("line").value,
    direction: null,
    origin: $("origin").value,
    destination: $("destination").value,
    arriveBy: $("arrive-by").value,
    walkMinutes: Number($("walk").value),
    bufferMinutes: Number($("buffer").value),
  };
  j.direction = directionFor(j.line, j.origin, j.destination);
  return j;
}

// ---- Saving -----------------------------------------------------------------

function loadSaved() {
  const fromHash = journeyFromFragment(location.hash);
  if (fromHash) return fromHash;
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    return isValidJourney(stored) ? stored : null;
  } catch {
    return null;
  }
}

function save(j) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(j));
  } catch {
    // Private windows may refuse storage; the address still holds the journey.
  }
  history.replaceState(null, "", journeyToFragment(j));
}

// ---- Planning card -----------------------------------------------------------

function minutes(seconds) {
  return `${(seconds / 60).toFixed(1)} min`;
}

function stat(label, value) {
  const div = el("div");
  div.append(el("dt", {}, label), el("dd", {}, value));
  return div;
}

function renderPlan() {
  const box = $("plan");
  const destinationEntry = origin?.destinations.find((d) => d.naptan_id === journey.destination);
  if (!destinationEntry) {
    box.replaceChildren(
      el("p", {}, "Planning data is not published yet for this journey."),
      el("p", { class: "muted" }, "The live times above still work; the arrival estimate needs a published run time."),
    );
    return;
  }
  const arriveBy = parseClock(journey.arriveBy);
  const plan = planLeaveBy({
    slots: destinationEntry.slots,
    arriveByMinute: arriveBy,
    walkMinutes: journey.walkMinutes,
    bufferMinutes: journey.bufferMinutes,
  });
  if (plan.status === "outside_slots") {
    box.replaceChildren(el("p", {}, "This arrival time is outside the published weekday windows (06:00 to 10:00, 16:00 to 19:00)."));
    return;
  }
  if (plan.status === "withheld") {
    box.replaceChildren(
      el("p", {}, `Figures for this line are held back while they are checked (${plan.slot.start} to ${plan.slot.end} window).`),
    );
    return;
  }
  if (plan.status === "not_enough_data") {
    box.replaceChildren(
      el("p", {}, `Not enough data yet for the ${plan.slot.start} to ${plan.slot.end} window.`),
      el("p", { class: "muted" }, "Figures appear once a window has at least 20 matched trains over 4 weeks on days that passed the quality check."),
    );
    return;
  }
  const s = plan.slot;
  const dl = el("dl", { class: "stats" });
  dl.append(
    stat("Trains seen a day", `${s.trains_seen_per_day ?? "?"} of ${s.scheduled_per_day ?? "no timetable"}`),
    stat("Run time, median", minutes(s.run_seconds.p50)),
    stat("Run time, p90", minutes(s.run_seconds.p90)),
    stat("Wait, typical", minutes(s.wait_seconds.typical)),
    stat("Wait, bad day (p90)", minutes(s.wait_seconds.worst_p90)),
    stat("Weekdays behind it", `${s.days_counted} (${s.runs} trains)`),
  );
  box.replaceChildren(
    el("p", { class: "leave-by" }, `Leave by ${clock(plan.leaveBy)} on a weekday`),
    el(
      "p",
      { class: "muted" },
      `To be on the platform by ${clock(plan.atPlatformBy)} for the ${s.start} to ${s.end} window, arriving by ${journey.arriveBy} with ${journey.bufferMinutes} min in hand on a p90 run and a bad-day wait. On a typical day ${clock(plan.typicalLeaveBy)} is enough.`,
    ),
    dl,
  );
}

// ---- Live -------------------------------------------------------------------

function renderVerdict(verdict) {
  const box = $("verdict");
  box.dataset.kind = verdict.kind;
  box.replaceChildren(el("p", { class: "headline" }, verdict.headline), el("p", { class: "detail" }, verdict.detail));
  const items = verdict.trains.map((t) => {
    const li = el("li");
    li.append(el("span", { class: "time" }, t.arrival));
    li.append(el("span", {}, t.towards ? t.towards.replace(/ Underground Station$/, "") : "Destination not shown"));
    let note;
    if (t.kind === "destination_not_shown") note = "destination not shown: may not call at your stop";
    else if (t.kind === "may_not_call") note = "may not call at your stop (another branch)";
    else if (!t.canReach) note = "too soon to reach";
    else if (t.makesIt === null) note = "";
    else note = `${t.makesIt ? "makes" : "won't make"} ${journey.arriveBy} (about ${t.atDestination}, p90)`;
    if (note) li.append(el("span", { class: "note" }, note));
    return li;
  });
  $("trains").replaceChildren(...items);
}

async function refreshLive() {
  if (!journey) return;
  const now = Date.now();
  const arriveByMs = londonInstant(now, parseClock(journey.arriveBy));
  const destinationEntry = origin?.destinations.find((d) => d.naptan_id === journey.destination);
  const reach = destinationEntry
    ? reachFromPublished(destinationEntry)
    : reachFromStops(
        BUILT_IN_LINES.find((l) => l.line === journey.line)?.directions[journey.direction] ?? [],
        journey.origin,
        journey.destination,
      );
  const slot = destinationEntry ? slotAt(destinationEntry.slots, now) : null;
  try {
    const arrivals = await getJson(`${TFL}/StopPoint/${encodeURIComponent(journey.origin)}/Arrivals`);
    const verdict = assessArrivals({
      arrivals,
      journey: { ...journey, destinationName: stationName(journey.destination) },
      reach,
      runP90Seconds: slot?.status === "ok" && slot.run_seconds ? slot.run_seconds.p90 : null,
      nowMs: now,
      arriveByMs,
      missShare: boardsMissShare(slot),
    });
    if (arriveByMs < now) verdict.detail = `${journey.arriveBy} has passed for today. ${verdict.detail}`;
    renderVerdict(verdict);
    $("live-stamp").textContent = `Boards read at ${londonClock(now)}; refreshes every 30 s while this page is open.`;
  } catch {
    renderVerdict({
      kind: "error",
      headline: "Could not read TfL's boards just now.",
      detail: "This says nothing about the trains. Trying again in 30 s.",
      trains: [],
    });
  }
}

function stationName(id) {
  if (index?.stations[id]) return index.stations[id];
  for (const line of BUILT_IN_LINES) {
    const stop = line.directions.inbound.find((s) => s.naptan_id === id);
    if (stop) return stop.name;
  }
  return id;
}

function schedule() {
  clearInterval(timer);
  timer = null;
  if (journey && document.visibilityState === "visible") {
    refreshLive();
    timer = setInterval(refreshLive, REFRESH_MS);
  }
}

// ---- Wiring -----------------------------------------------------------------

async function useJourney(j) {
  journey = j;
  origin = null;
  if (index && j.direction) {
    try {
      origin = await getJson(`${DATA}/journeys/${j.line}/${j.direction}/${j.origin}.json`);
    } catch {
      origin = null;
    }
  }
  renderPlan();
  schedule();
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const j = readForm();
  const error = $("form-error");
  if (!isValidJourney(j) || !j.direction) {
    error.textContent = j.direction
      ? "Check the arrival time, walk and buffer."
      : "No train on this line goes from that station to that one. Pick the stations again.";
    error.hidden = false;
    return;
  }
  error.hidden = true;
  save(j);
  useJourney(j);
});

$("line").addEventListener("change", () => fillForm({ ...readForm(), origin: undefined, destination: undefined }));
document.addEventListener("visibilitychange", schedule);

async function start() {
  try {
    index = await getJson(`${DATA}/journeys/index.json`);
  } catch {
    index = null; // Not published yet: the built-in Victoria line only.
  }
  const saved = loadSaved();
  fillForm(saved);
  if (saved) {
    saved.direction = directionFor(saved.line, saved.origin, saved.destination);
    if (saved.direction) useJourney(saved);
  }
}

start();
