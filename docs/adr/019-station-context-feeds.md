# ADR-019: Collect lift disruptions, live busyness, BikePoint and status ahead

Date: 2026-11-20
Status: proposed (D77)

## Context

TfL publishes several live feeds that it keeps no history of. A day not collected is lost for good. Planned products need them:

- **Step-free reliability.** Draft PR #47 infers lift outages from stop disruptions. TfL's dedicated lift feed names each lift and every station with a lift out of service.
- **Station "misery" scores and crowding-aware advice.** These need live busyness per station over months, not a typical-day profile.
- **"Should I cycle instead?"** This needs the cycle hire docks' bikes and free spaces over time.
- **Planned-closure foresight.** This needs what TfL announced in advance, to set against what then ran. The live status only shows planned works on the day.

The probes below were made without a key on 20 November 2026, between 03:20 and 03:40 UTC:

| Feed | Request | Result |
| --- | --- | --- |
| Lifts | `GET /Disruptions/Lifts/v2` | 200, 20 stations, 6.7 KB (1.5 KB gzipped). Each entry has `stationUniqueId` (940GZZ, 910G or HUB id), `disruptedLiftUniqueIds` and `message`. Not CDN-cached (`cf-cache-status: DYNAMIC`). |
| Busyness | `GET /StopPoint/940GZZLUKSX/Crowding/victoria` | 404, as first found. Also 404 for Oxford Circus. |
| | `GET /crowding/940GZZLUKSX/Live` (also `/Crowding/...`) | 200: `{"dataAvailable":true,"percentageOfBaseline":0.0016,"timeUtc":"2026-11-20T03:21:00.000Z","timeLocal":...}`. `cache-control: no-store`. |
| | `GET /crowding/HUBKGX/Live` | 200 with `dataAvailable:false`: hub ids give no data. |
| | `GET /crowding/940GZZLUOXC/Live`, `.../940GZZLUWLO/Live` | 200 with `dataAvailable:false` at 03:26 UTC on a Thursday night, when those stations were closed. |
| | `GET /crowding/910GPADTLL/Live` | 200 with data: Elizabeth line ids work too. |
| | `GET /crowding/940GZZLUKSX` | 200: the typical profile by weekday in 15-minute bands. This is not collected; it is not live and can be fetched at any time. |
| BikePoint | `GET /BikePoint` | 200, 800 docks, 2.2 MB (89 KB gzipped). Counts are text `additionalProperties`: `NbBikes`, `NbStandardBikes`, `NbEBikes`, `NbEmptyDocks`, `NbDocks`, each with `modified`. |
| Status ahead | `GET /Line/Mode/{modes}/Status/{from}/to/{to}` | **404** for every mode list tried. |
| | `GET /Line/{20 line ids}/Status/2026-11-20/to/2026-12-04` | 200, 20 lines, 92 KB (7.6 KB gzipped), in the `Line/Mode/{modes}/Status` shape. It held weekend closures up to 30 November, filed `PlannedWork` with future validity periods, and each disruption's `created` date. |

The live busyness endpoint is therefore `crowding/{naptan}/Live` with a 940GZZLU station id. The `StopPoint/{id}/Crowding/{line}` form the brief started from does not exist.

**Terms.** TfL's transport data licence (https://tfl.gov.uk/corporate/terms-and-conditions/transport-data-service, read 20 November 2026) is based on the Open Government Licence 2.0. The parts this relies on:

> You are free to: Copy, publish, distribute and transmit the Information; Adapt the Information and Exploit the Information commercially and non-commercially …

> You must, where You do any of the above: Acknowledge TfL as the source of the Information by including the following attribution statement 'Powered by TfL Open Data' … Limit traffic requests up to a maximum of 500 calls per minute per data feed.

> This Licence does not … permit You to: Use data from the Oyster, Congestion Charging and Santander Cycles websites to populate or update any other software or database …

Storing the responses is copying the Information, which the licence allows. Anything published from them carries "Powered by TfL Open Data", as the league already does (D63). The Santander Cycles exclusion covers that website, not the API's `BikePoint` feed, which is read here.

## Decision

1. **A new Lambda, `mind-the-data-context`, every minute.** It is separate from the poller, so these feeds can never use up the league feeds' 45-second budget, their concurrency or their memory. It uses the same client, retries, envelope, raw layout (ADR-003), logs and metrics. It runs at 512 MB, concurrency 10 and a 45-second budget, with no retries: the next minute is the retry. The scheduled minute decides what each run fetches:

   | Feed (`raw/{feed}/`) | Request | Cadence | Calls a minute |
   | --- | --- | --- | --- |
   | `lift_disruption` | `/Disruptions/Lifts/v2` | every minute | 1 |
   | `crowding` | `/crowding/{naptan}/Live` per Tube station | each station every 5 minutes, a fifth of the stations each minute | 48 to 63, 54 on average |
   | `bike_point` | `/BikePoint` | minutes where minute % 5 = 2 | 0.2 |
   | `line_status_ahead` | `/Line/{every configured line}/Status/{today}/to/{today+14}` | 00:07, 06:07, 12:07 and 18:07 UTC | under 0.01 |

2. **Lifts every minute.** It is one call, and the cost is one more S3 object a minute (about $0.23 a month). An outage that starts and ends inside 5 minutes would otherwise be missed or mistimed.
3. **Busyness for every Tube station, spread over the minute.** `config/crowding_stations.yaml` lists the 270 station-level 940GZZLU ids. It is generated by `scripts/discover_crowding_stations.py` from `Line/{id}/StopPoints` and checked at load: ids are unique and 940GZZLU, and there are at most 300. Each station keeps a fixed minute slot, from a CRC-32 of its id (as ADR-016 does for timetables). Spreading the stations over 5 minutes keeps the rate flat, rather than 270 calls in one burst every fifth minute.
4. **Status ahead 4 times a day, not once.** A lost invocation then costs 6 hours, not a day of announcements. Successive fetches also show when TfL changes or withdraws a planned closure. The lines are named in the path, because the Mode form answers 404.
5. **Missing data is never good data.** Failed requests are written as failure envelopes, as everywhere else. A busyness answer with `dataAvailable:false` is stored as unknown (null share), never as an empty station. A successful lift poll that lists no station keeps one row in staging, so it can be told apart from a missing minute.
6. **Raw and staging only.** There is a Glue table per feed and four `stg_` models, each with tests and a hand-written unit test: `stg_lift_disruption`, `stg_crowding`, `stg_bike_point` and `stg_line_status_ahead`. The models and their sources are off until `context_feeds_enabled` is set. Otherwise every transform run would fail on the missing tables between merge and the infra apply. There are no marts and nothing is published.

### Request budget

| Source | Calls a minute, average | Peak minute |
| --- | --- | --- |
| Poller (ADR-016): 50 station boards, 20 line boards, status, disruption, stop disruption every 5th minute | 72.2 | 73 |
| Reference fetch, daily at 03:00 London: 153 to 197 calls at concurrency 4 over about 4 minutes (ADR-007) | under 0.2 over a day | about 50 |
| **Context collector (this ADR)** | **55.2** | **64** (the minutes with 63 stations, plus lifts) |
| **Total** | **about 128** | **about 187** |

The peak is 37% of the 500-a-minute key limit, which the licence also sets per feed, so the real headroom is larger. `tests/unit/test_context.py` checks that the largest run stays at 70 calls or fewer and that the combined peak stays within 40% of the limit. A full Tube list of 300 stations would add 6 calls a minute.

### Cost

From the payload sizes above, per 30 days:

| Item | Amount | USD |
| --- | --- | --- |
| S3 PUT: 43,200 lift + 43,200 busyness + 8,640 BikePoint + 120 status-ahead objects | 95,160 requests | 0.50 |
| S3 storage: BikePoint about 0.77 GB, busyness about 0.13 GB, lifts about 0.08 GB | about 1 GB more each month | about 0.025 for each month kept |
| S3 GET: Athena reads 3 days of objects a transform run | about 285,000 requests | 0.12 |
| Athena: 4 staging models over 3 days of raw (about 100 MB) a day | about 3 GB | 0.02 |
| Lambda: 43,200 invocations, about 1 s at 512 MB, so about 22,000 GB-s | inside the free tier with the poller (about 216,000 GB-s) | 0 |
| CloudWatch alarms: 2 new alarms (one has 2 metrics) | 3 alarm metrics, past the 10 free | up to 0.30 |
| CloudWatch custom metrics: `ObjectsWritten` and `RequestFailures` for `lift_disruption` only. The other three feeds emit none; their counts stay in the log line | 2 metrics, past the 10 free | 0.60 |
| CloudWatch logs: one line a run (about 65 MB) | inside the free tier | 0 |
| **Total** | | **about 1.55 a month at first, plus 0.025 for each month of data kept** |

Today's bill is about $2 a month (`docs/cost.md`), so this brings it to about $3.55, inside the $2 to $10 range and the £5 budget (H5). S3 PUT requests and the two lift metrics are most of the cost. Each feed's metrics cost $0.60 a month, which is why only the alarmed feed emits them. If the cost needs to come down, lifts can move to every 5 minutes, which saves $0.18.

## Consequences

- The busyness endpoint was only checked without a key, at night. Its shape is the same with or without a key; stations closed at night returned `dataAvailable:false`. In the first week, check the daytime share of rows with data per station (runbook, Station context feeds). If whole stations never have data, take them out of the list.
- `percentageOfBaseline` is a share of TfL's own baseline for each station, not a count of people. Values are comparable over time at one station, not between stations. Any product built on it has to say so.
- Lifts arrive under station ids of three kinds (940GZZ, 910G, HUB). Joining them to `dim_station` needs a mapping, built when the step-free product is.
- The ops health check (`ops/health.py`) does not watch the new feeds yet. The `lift_disruption` no-objects alarm and the context error-rate alarm cover the collector. Add the feeds to the health check once they have a week of history.
- A new Tube station needs the list regenerated. The loader stops the list growing past 300.

## Deferred

- **Journey Planner polling** (`/Journey/JourneyResults`). It is heavy, needs a chosen set of origin and destination pairs, and is not history TfL withholds: a journey can be planned at any time.
- **Bus arrivals** (`/Mode/bus/Arrivals`, `/StopPoint/{id}/Arrivals` for bus stops). There are tens of thousands of stops, so it needs its own sampling design and budget. The value is real but it is a separate product.
- **Road disruptions** (`/Road/all/Disruption`). Off the rail scope.
- River and cable car arrivals, `/Vehicle/{ids}/Arrivals` and AirQuality: low value for now.
- Busyness for Elizabeth line, Overground and DLR stations. The endpoint answered for Paddington's 910G id. Adding them is a config change, once the Tube data has shown its worth and its gaps.
- The bulk open data (NUMBAT, station entry and exit counts, cycle hire journeys). It is kept by TfL, so there is no urgency to collect it.

## Alternatives

- **Add the feeds to the poller.** It is one less function, but 55 more calls a minute would share the league feeds' budget and concurrency, and a BikePoint parse would share their memory. One bad feed could cost a minute of league data.
- **All 270 busyness calls every fifth minute.** That is one object instead of five, saving about $0.18 a month, but the calls come in bursts of 270, and with the poller and reference fetch a peak minute reaches about 395 of 500.
- **Status ahead once a day.** That is what was first asked for, but a single lost invocation would cost a whole day, and the minute Lambda has no retries.
- **Station list from route sequences at run time.** It would always be current, but every run would need either S3 read access or 11 more calls a minute, for a list that changes about once in a decade.
