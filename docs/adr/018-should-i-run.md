# ADR-018: Should I Run, a journey planner on 15-minute windows

Date: 2026-11-20
Status: proposed (D76, exploratory)

## Context

The every-station feed (M8, ADR-016) follows each train by vehicle id from station to station. That makes a personal question answerable for a journey made often: "I need to be at B by 09:00, A is a 6-minute walk away; when do I leave?" Journey planners answer it from the timetable. This data can answer it from what trains actually did, and then from what the boards show on the morning.

The product has two halves:

- **Planning, from history.** For a saved journey (line, A, B, arrive-by, walk to A, buffer), how many trains really come in your 15-minute window, the typical and the bad-day wait at A, and the run time from A to B (median and p90). From these, a suggested leave-by time.
- **Live, on the morning.** The next trains at A that reach B, and one sentence: "Leave now: about 3 min to spare (p90)", "The next train you can reach won't make 09:00", or "No trains to B shown on the boards".

A worked example from dev, Victoria southbound, King's Cross to Victoria, weekdays 08:00 to 08:30, 4 to 17 November (measurement-station data): 10 to 18 trains a day against 18 timetabled, run time p10 8 min 29 s, median 9 min 12 s, p90 10 min 11 s.

Constraints:

- Tube timetables carry no trip ids, and trains run every 100 seconds or so at peak. A named departure ("the 08:12") cannot be matched to a train.
- The main site (karshvashi.com/tube) is static with a strict CSP and no client-side fetches. Live times need the browser to call TfL.
- One person runs the project; nothing here may add server state, accounts or a second platform.
- Missing data is never good news (PLAN, methodology section 1).

## Decision

1. **The unit is a 15-minute window, not a named departure.** Weekday slots from 06:00 to 10:00 and 16:00 to 19:00 (`journey_slot_windows`). Named departures for lines with stable trip ids (Elizabeth line, Overground) are phase P3.
2. **Linked lines only.** Runs need a vehicle id that follows the train: the lines with `link_by_vehicle_id` in `seed_vehicle_id_lines` (Bakerloo, Central, Circle, Hammersmith & City, Jubilee, Metropolitan, Northern, Piccadilly, Victoria). Not the District (ids not stable, D27), DLR (no ids), tram or Waterloo & City.
3. **Models**, tagged `exploratory`; the daily prod build now excludes that tag, so they run in dev only until accepted:
   - `int_station_vehicle_runs`: for each pass at A, the vehicle's first pass at B after it, where B follows A on a route of the latest route sequence in that direction, within `journey_max_run_minutes` (90) and before the vehicle's next pass at A. A branch train that does not call at B gives no run. An id seen twice at A with no B between gives no run for the first pass, rather than one stretched across two trips.
   - `rpt_station_reach`: which board destinations (`destinationNaptanId`) from A call at B, with an `is_ambiguous` flag where another route from A reaches the same destination without calling at B.
   - `rpt_journey_slots`: per line, direction, A, B and slot, over weekdays whose A hour was observed and passed the quality gate (ADR-014): runs and run time p10/p50/p90, trains seen per day against the timetable, the typical wait (the median day's expected wait for a random arrival) and the bad-day wait (p90 over days of the day's longest gap).
4. **Publication threshold.** A slot's figures are published only when the slot passes the gate and has at least 20 matched runs over at least 4 ISO weeks (`journey_min_runs`, `journey_min_weeks`). Otherwise the slot is written as `not_enough_data` with null figures.
5. **Files behind a flag.** `journeys/index.json` and `journeys/{line}/{direction}/{origin}.json`, written only with `JOURNEYS_ENABLED=true` (default off). `SCHEMA_VERSION` is unchanged; the contract is in `docs/site-integration.md` as not yet published.
6. **The page lives on its own origin**, for example `run.karshvashi.com`, as plain static files (`web/should-i-run/`) with a CSP of `connect-src https://api.tfl.gov.uk https://data.karshvashi.com`. The main site's CSP and its no-client-fetch rule stay as they are. The prototype lives in this repo, not the website repo: it is a separate origin with no build step, and the website has its own phase plan.
7. **The browser calls TfL directly**, without a key. TfL sends `access-control-allow-origin: *`, and each visitor stays far under the anonymous limit (about 50 requests a minute; the page makes 2 a minute while visible). **The project's key is never sent to a browser.**
8. **No accounts, no server state.** The journey is kept in `localStorage` and the URL fragment, which never reaches a server.
9. **No push notifications in v1.** Push needs a server that stores each person's journey and subscription, which is personal data under UK GDPR, plus a second platform to run. Deferred to P4.
10. **Wording.** Never tell anyone to run. "Leave now: about 3 min to spare (p90)." Every view says "Estimates, not guarantees". A train missing from the boards is "not shown", never "cancelled". No trains shown is never "you're fine". Attribution "Powered by TfL Open Data", with no roundel or TfL branding.

### Phases

| Phase | Scope | Gate |
| --- | --- | --- |
| P0 | This PR: models in dev, files behind a flag, prototype page with the Victoria line built in | Review |
| P1 | Models in the prod build; four weeks of every-station history | Run times checked against the timetable on 3 lines; Northern anomaly explained (below) |
| P2 | `JOURNEYS_ENABLED=true`; the page on its own origin | Owner sign-off on wording and hosting (D76) |
| P3 | Named departures on lines with stable trip ids (Elizabeth line, Overground) | Trip-id runs validated |
| P4 | Optional alerts | A privacy design, if ever |

## Alternatives considered

- **Named departures on the Tube.** No trip ids; the "08:12" in a timetable cannot be tied to a train on the boards.
- **Timetable run times.** Available for every pair, but they are the plan, not what ran; the point is the gap between the two.
- **Server-side live proxy with the project key.** It would put the key and every user's journey behind one server, and make the project responsible for an always-on service. The anonymous browser call needs neither.
- **A page inside the main site.** Needs client-side fetches to TfL, which the site's CSP and design rule out.
- **Accounts or saved journeys on a server.** Personal data with no benefit over the fragment.
- **Publishing every slot's figures with a confidence flag.** Readers act on numbers, not flags. Thin slots carry none.

## Consequences

- One person's journey is computed in their browser from public files; the project holds nothing about anyone.
- Risks:
  - **Liability.** People may miss trains on these numbers. The wording rules, the p90 framing and the disclaimer limit the claim; the page never instructs.
  - **Board artefacts.** A pass is the expected arrival at a train's last sighting. On the Northern line from Camden Town, Bank-branch runs read 2 to 5 minutes longer than the timetable suggests (Camden Town to Euston p50 4 min 43 s, one stop), while the Charing Cross branch looks plausible. Until that is explained, Northern figures should stay unpublished.
  - **The boards miss trains** (about 15% of timetabled trains are never listed, ADR-014). Waits are computed from matched trains only, so they read long, never short; trains seen per day is shown against the timetable.
  - **TfL's anonymous limit or CORS could change.** The page then says it cannot read the boards, which says nothing about the trains.
  - **Size.** `int_station_vehicle_runs` is a full rebuild bounded only by the slot windows. On the every-station feed it held about 350,000 runs for 17 November alone. It needs an incremental, date-bounded build before P1.
  - **Fetching the marts.** The publisher reads both tables in full (about 250,000 slot rows at full scale) through paged `GetQueryResults`; an `UNLOAD` is the likely replacement.
- Revisit at P1, after four weeks of every-station data.
