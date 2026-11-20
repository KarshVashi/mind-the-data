# ADR-020: Typical passenger demand from TfL's NUMBAT and annual counts

Date: 2026-11-20
Status: proposed (exploratory, toward v2; D78)

## Context

The league weighs a minute of Severe Delays on the Waterloo & City the same as one on the Central line. Only the clock varies the weight (peaks 2, weekends 0.75). The plan lists a passenger-weighted table as phase 2 (PLAN, "Out of scope"). It also asks where the network is fragile: which links, if closed, would affect the most passengers.

TfL publishes typical demand as bulk open data at `crowding.data.tfl.gov.uk`. The page at that address is a script-driven listing that renders empty to a plain fetch. The bucket behind it (`https://s3-eu-west-1.amazonaws.com/crowding.data.tfl.gov.uk/?list-type=2`) listed 505 objects on 20 November 2026. The relevant ones:

| Path | Size | Published | What it holds |
| --- | ---: | --- | --- |
| `NUMBAT/NUMBAT 2025/NBT25{MON,TWT,FRI,SAT,SUN}_Outputs.xlsx` | 11.6 to 12.5 MB each | 2026-08-10 | Typical autumn 2025 day, per quarter hour: `Link_Loads` (passengers on trains between adjacent platforms, per line and direction), `Line_Boarders`, `Station_Entries`/`Exits`, `Station_Boarders`/`Alighters`, `Station_Flows`, `Link_Frequencies` |
| `Annual Station Counts/2025/AC2025_AnnualisedEntryExit_public.xlsx` | 106 KB | 2026-08-14 | Typical entries and exits per station and day type, and annualised totals |
| `NUMBAT/NUMBAT 2024/OD Data/NBT24*5d_od_network_tb_lf_o.csv` | 18 to 27 MB each | 2025-11-25 | Origin-destination flows by time band (2024; no 2025 OD files yet) |
| `NUMBAT/NBT16-25_AnnualisedBoarders_Outputs.xlsx` | 24 KB | 2026-08-14 | Annual boarders per line, 2016 to 2025 |

The NUMBAT cover sheet describes the data as "the travel demand on a typical autumn Monday, Tuesday to Thursday, Friday, Saturday and Sunday at all stations and lines of the London Underground, London Overground, Docklands Light Railway and Elizabeth Line", with "days affected by major disruptions, events and closures excluded", and assuming "a full train service operated as scheduled". It rates link loads "High confidence in most results ... Lower confidence for routes that parallel non-TfL lines". Its `Line_Boarders` sheet lists London Trams with zero boarders.

### Licence

TfL's transport data terms (<https://tfl.gov.uk/corporate/terms-and-conditions/transport-data-service>, page edited 2026-07-08, read 2026-11-20) say:

> These licence terms and conditions apply to TfL's free transport data service and are based on version 2.0 of the Open Government Licence with specific amendments for Transport for London (the "Licence").

> You are free to: Copy, publish, distribute and transmit the Information; Adapt the Information and; Exploit the Information commercially and non-commercially for example, by combining it with other Information, or by including it in Your own product or application

> You must, where You do any of the above: Acknowledge TfL as the source of the Information by including the following attribution statement 'Powered by TfL Open Data'

> This Licence does not grant You any right to use the Information in a way that suggests any official status or that TfL endorses You or Your use of the Information.

The workbooks carry "(C) Copyright Transport for London 2026". TfL's open data page lists "TfL detailed annual rail and Tube demand dataset" (NUMBAT) and "TfL Annual Counts (Entry/Exit) for rail and Tube" among its open data, but does not repeat the licence on those entries. The reading here is that the same terms apply, and that they allow small derived tables in this repository with the attribution. The Ordnance Survey attribution in the same terms concerns map data, which none of these files contain. **Owner check:** confirm this reading before anything derived from these files is published (D78).

## Decision

1. **Pin NUMBAT 2025 and the 2025 annual counts** by URL and sha256 in `scripts/fetch_demand_data.py`. The script downloads them to a cache outside the repository (default `~/.cache/mind-the-data/demand`, about 61 MB) and refuses a cache inside it. A file TfL replaces in place fails the hash check and stops the script.
2. **Commit only small derived seeds**, written by the script and never by hand:
   - `seed_line_demand` (55 KB): boarders and summed link loads per NUMBAT line, service day type and London clock hour;
   - `seed_link_demand` (136 KB): each link's typical daily load per day type, and its Monday-to-Thursday 07:00 to 10:00 load;
   - `seed_station_demand` (23 KB): typical entries plus exits per station.

   A hand-written `seed_demand_line_map` maps NUMBAT's line names to TfL ids. `scripts/fetch_demand_data.py --check` shows whether the seeds match the pinned files.
3. **Day types.** NUMBAT's traffic day runs 05:00 to 04:59, so hours 0 to 4 belong to the previous calendar day (`service_day_type`). The project's `mon_thu` is NUMBAT's Monday and Tuesday-to-Thursday days mixed 1:3.
4. **No new live collection** and no new infrastructure: the seeds load with the rest in `dbt build`. The exploratory model that reads them (`rpt_line_weekly_passenger_weighted`, D78) is disabled in the prod target and tagged `exploratory`. The critical-links analysis is a script that reads dev (`scripts/analysis/critical_links.py`).
5. **Refresh yearly.** TfL publishes each autumn's NUMBAT and annual counts in the following August to November (2025's in August 2026, 2024's NUMBAT in November 2025). Then move the pins, rerun the script and review the seed diff (runbook, "Demand data").

## Consequences

- Demand is a typical autumn 2025 day, not the week being scored. A strike, an event or a closure changes who travels; the weights do not follow.
- NUMBAT reports Circle and Hammersmith & City as one line ("H&C and Circle"). The map gives each half; that split is an assumption (D78).
- Trams have no NUMBAT demand. They get no passenger-weighted figures, and are unranked `no_demand_data`, never treated as zero.
- About 214 KB of seeds in the repository, refreshed once a year. The raw workbooks are never committed.
- Anything published from these seeds must carry "Powered by TfL Open Data" and must not suggest TfL endorses the figures.

## Alternatives

- **Keep the files in S3 and read them through an external table.** That needs a bucket prefix, a Glue table and IAM for a file that changes once a year. It is worth doing only if the 2024 origin-destination files (about 110 MB) are wanted for a routing model.
- **Line boarders only (the 24 KB annualised boarders file).** It is simpler, but has no hours, so it cannot weight a peak minute above a late one.
- **Station entry/exit counts as the weight** (the plan's wording). They count people at stations, not on lines, and an interchange-heavy line would get demand that belongs to its neighbours. They are kept for the station ranking only.
- **Parse the workbooks with a spreadsheet library.** The script reads them with the standard library (`mind_the_data.demand.xlsx`) to avoid a new dependency for a yearly job.
