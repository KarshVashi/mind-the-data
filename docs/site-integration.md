# Site integration

How karshvashi.com shows the Tube League Table. This is the M5 task "`docs/site-integration.md`: how the Astro site consumes the files" (PLAN 10, M5). The site lives in its own repo (`KarshVashi/karsh-vashi-website`); nothing here changes it. Section 8 below is the checklist to carry out there.

The rule from PLAN 8 holds throughout: the website only reads the published files. It never recomputes a metric, and it never shows missing data as Good Service.

---

## 1. The site today

- **Astro 7, fully static** (`output: 'static'`, trailing-slash directory URLs), plain CSS with tokens in `src/styles/tokens.css`, MDX content collections (`projects`, `pages`), JavaScript only where a diagram needs it.
- **Hosting:** Cloudflare Pages, built from `main`, preview deploys on pull requests. The site's portability rules forbid Pages Functions, `_headers`/`_redirects` files and any KV, D1 or R2 binding: it must move to another static host with no code changes.
- **CSP** is a `<meta>` tag with per-page hashes (`astro.config.mjs`): `connect-src 'self' https://cloudflareinsights.com`, `img-src 'self' data:`, and inline `style` attributes are blocked. So a browser on the site cannot fetch JSON or load images from another origin, and components size and colour SVG through attributes, not `style=`.
- **Navigation:** main nav is Projects and About (flags in `src/config.ts`); Now, How I work, Uses and CV in the footer. Explainers and Writing are planned but not built.
- **Projects:** `src/content/projects/<slug>.mdx` renders at `/projects/<slug>/` with a metadata rail (status, stack, published, repo and docs links). The live projects are `wiki-stream-lake` and `movie-ratings-analytics`, written as architecture plus measured findings, with build-time `BarChart` tables and animated `FlowDiagram`s.
- **Share images:** every page gets a 1200×630 PNG rendered at build time by Satori (`src/lib/og.ts`, served from `/og/...png`).
- **Checks in CI:** `astro check`, build, link check, axe (pa11y) and Lighthouse.
- **Voice and content rules** (the site repo's contributor rules): first person, plain, a builder sharing work; no invented claims (`TODO(Karsh)` where substance is missing); project facts must match the project's repo.

## 2. Recommended architecture

**Build-time fetch from a public R2 custom domain, rebuilt weekly by the Pages deploy hook.** The site's build reads `meta/index.json` and the files it lists, validates them, and renders every page to static HTML. The weekly publish run uploads to R2 and then calls the deploy hook, which starts a fresh production build of the site.

```
 Mon 06:30 London                                     Cloudflare
 publish-weekly.yml (mind-the-data)
   export.py ──► build/publish/ ──► R2 bucket ───────► data.karshvashi.com
     │            league/ lines/      (S3 API,          (public custom domain,
     │            meta/ cards/         boto3)            cached at the edge)
     │                                                        │
     └── POST deploy hook ──► Pages build of karsh-vashi-website (main)
                                 astro build
                                   fetch meta/index.json ◄────┘ (+ the files it lists)
                                   validate (zod), render /tube/** to HTML,
                                   copy share cards into /og/tube/*.png
                                 ──► karshvashi.com (static files only)
```

### What the deploy hook triggers

A Pages deploy hook is a secret URL; a `POST` to it starts a **production build of the latest commit on `main`**, exactly as a push would (same build command `npm run build`, same `.nvmrc`). It does not run the site's GitHub Actions CI, so validation the site depends on must happen inside `astro build` itself. If the build fails, Pages keeps serving the previous deployment, so a bad or missing file leaves last week's pages up rather than a broken page.

### Options compared

| | Build-time fetch + deploy hook (recommended) | Client-side fetch | Vendored JSON snapshot |
|---|---|---|---|
| Fits the site's rules | Yes: static HTML, no binding, no CSP change | No: needs `connect-src` for the data origin, a loading state, and JS on a page that does not need it | Yes |
| Freshness | Within minutes of the Monday publish | Immediate | Only when someone commits a new snapshot |
| CORS | Not needed (Node fetch at build) | Needed on R2 | Not needed |
| SEO, OG, a11y, Lighthouse | Full HTML, per-week OG images | Empty shell until JS runs; OG cannot reflect the week | Full HTML |
| Failure mode | Build fails, old site stays | Page shows an error to readers | None, but goes stale silently |
| Use | v1 onwards | Not used | v0 only (Wednesday post), and as the dev/test fixture |

### Fetching and validation in the site

- One module (`src/lib/tube/data.ts`) is the only place that reads data. It has two sources behind one interface: **remote** (the R2 base URL in `src/config.ts`, public, not a secret) and **local** (a directory of files in the same layout, used for v0 and for dev and CI). An environment variable such as `TUBE_DATA_DIR` picks local; otherwise remote.
- Read `meta/index.json` first; fetch `league/latest.json`, every `weeks[].path` and every `lines[].path` it lists, plus `meta/methodology.json`. Total size is tens of KB per week.
- Validate with zod (already available as `astro/zod`) against the fields the site uses. Refuse a `schema_version` whose major part differs from the one the site was written for. A failed fetch or validation fails the build; it never falls back to stale vendored data in production.
- Append a cache-busting query (`?build=<timestamp>`) to every remote fetch, so a build started by the deploy hook never reads a copy the edge cached before the upload (Cloudflare's cache key includes the query string).

### Images

Share cards (`cards/{iso_week}.png`, 1200×675 and `-1080x1350`) are fetched at build time and emitted as the site's own files by an endpoint route (as `src/pages/og/[...route].png.ts` already does), for example `/og/tube/2026-W46.png`. They then pass `img-src 'self'` and survive a host move. No `<img>` points at R2.

### CORS

The site needs none. Set a read-only CORS rule on the bucket anyway, because the files are meant as open data (M7) and someone will fetch them from a browser: `GET` and `HEAD` from any origin, no credentials, `Content-Type` and `ETag` exposed. See section 9.

### Caching

| Object | `Cache-Control` set at upload | Why |
|---|---|---|
| `league/latest.json`, `meta/index.json`, `meta/methodology.json`, `lines/*.json` | `public, max-age=300` | Overwritten every Monday |
| `league/{iso_week}.json` | `public, max-age=3600` | Rewritten each run under D39, but changes only when the methodology or marts do |
| `cards/*.png` | `public, max-age=86400` | Rendered once per week |
| `open-data/{yyyy-mm}/*` (Parquet, `README.md`, `manifest.json`) | `public, max-age=3600` | Written once a month; a re-export replaces them |

The open data (M7, D63) goes up in its own monthly run, `manifest.json` last, with no deploy hook: the site does not read it. A page that links to it should link to `open-data/{yyyy-mm}/README.md` and keep the attribution "Powered by TfL Open Data" beside the link.

The build bypasses edge caching with its query string, so these values only matter to other readers. Upload order matters: week files, line files, cards and methodology first; `league/latest.json` and `meta/index.json` last; then the deploy hook. A reader then never sees an index that lists a file not yet uploaded.

## 3. Pages

Live data pages sit under `/tube/`: a short, stable URL for posts (`karshvashi.com/tube/`). The write-up sits with the other projects.

| URL | Source | Purpose |
|---|---|---|
| `/projects/tube-league-table/` | MDX in the `projects` collection | Case study: what it is, the architecture, the decisions, what the numbers do and do not say. Links to `/tube/`. |
| `/tube/` | `league/latest.json` | This week's league. The page people land on from a post. |
| `/tube/weeks/` | `meta/index.json` | Archive: every published week, newest first. |
| `/tube/weeks/{iso_week}/` | `league/{iso_week}.json` | One week's league, the same layout as `/tube/`, with previous and next links. |
| `/tube/lines/{line_id}/` | `lines/{line_id}.json` | One line's history. |
| `/tube/methodology/` | `meta/methodology.json` plus prose | How the numbers are made, the weights, thresholds and open decisions. |
| Later: `/tube/divisions/`, `/tube/season/`, `/tube/data/` | `divisions/`, `season/`, `open-data/` | M6 and M7, when those files exist. |

### Wireframes

**`/tube/`** (and `/tube/weeks/{iso_week}/`)

```
PageHeader   label "TUBE LEAGUE TABLE"   title "Week 46: 9 to 15 November"
             lede: one line on what the table ranks (declared view, Disruption Index, lower is better)
DataStamp    data through Sun 15 Nov 23:59 · completeness 99.1% · generated Mon 06:31 · git abc1234
             [PARTIAL WINDOW] banner when is_window_complete is false
Insight      the insight text (from the file, never written by the site)
LeagueTable  rank (=1 for ties) · movement ▲2 · chip + line name (link to line page) ·
             Disruption Index · Good Service % · incidents · median recovery ·
             [measured columns: "not measured yet" until views.measured = published]
             provisional rows marked * with the reason in a footnote list below the table
WorstIncident  line, start (London), duration, worst status, first reason (quoted)
Footer notes   methodology v0.x: open decisions D16…; "Powered by TfL Open Data"; links to
               archive, methodology, the JSON files
```

**`/tube/lines/{line_id}/`**

```
PageHeader   label = mode   title = line name, colour swatch   lede: rank this week, Good Service %
TrendChart   weekly Disruption Index (bars or line), provisional weeks hatched, partial weeks marked
RankStrip    rank per week, small multiples
BadHours     "typical bad hours": up to three London hours with disrupted share (reuse BarChart)
WorstWeek    the complete week with the highest Disruption Index, linked to that week's page
DataStamp    history from {first week}; completeness over the history
```

**`/tube/weeks/`**: a list (not cards) of weeks, newest first: ISO week, dates, the top and bottom lines with chips, a provisional count, link to the week. Later a heatmap of line × week (see components).

**`/tube/methodology/`**: prose (`TODO(Karsh)`, drawn from the repo's `docs/methodology.md` when M7 writes it) plus generated tables: severity weights, time bands, thresholds, the open decisions list linked to `docs/DECISIONS.md` if the repo is public.

**`/projects/tube-league-table/`**: same template as wiki-stream-lake: architecture `FlowDiagram` (TfL API → Lambda poller → S3 raw → Glue/Athena → dbt Iceberg marts → export → R2 → site), why each piece is there, the declared vs measured views, the fairness rules, and a "this week" panel with the top three and a link to `/tube/`. Stack from PLAN 3. `repo` only if the repo is public by then.

### Navigation

- v0: no nav change. The project appears in Projects (featured), and its page links to `/tube/`.
- v1: add `Tube league` to `mainNav` in `src/config.ts` once `/tube/` is live weekly; `aria-current` already works by path prefix. Add a small "This week" block on the home page (top three and bottom line, link to `/tube/`).
- `/tube/**` pages go in the sitemap automatically; `/tube/weeks/{iso_week}/` pages have canonical URLs of their own; `/tube/` carries the latest week.

## 4. Components

All in the website repo under `src/components/tube/`, using tokens, Plex Mono labels and hairline rules. Zero JavaScript unless noted.

| Component | Notes |
|---|---|
| `LineChip` | Swatch plus name. Line colours are data, not design tokens: draw the swatch as an SVG `rect` with a `fill` attribute (inline `style` is blocked by the CSP), with a `var(--rule)` stroke so Northern's `#000000` shows on the dark theme and Circle's `#FFCD00` on the light one. The name is in `--ink`, never text on the line colour, which avoids contrast failures. |
| `LeagueTable` | A real `<table>` with `scope` headers and a caption. Ranks with ties shown as `=1`. Unranked lines (no scored minutes) last with "–". On narrow screens (≤560px) each row becomes a two-line grid, as `BarChart` does: rank, chip and name; then the two headline numbers. Secondary columns move into a `<details>` per row. |
| `RankMovement` | `▲2`, `▼1`, `–`, or nothing when `rank_movement` is null (custom windows, first week). Text, not colour alone. |
| `ProvisionalMark` | `*` after the line name plus a numbered footnote listing `provisional_reasons[].detail` and the decision ids. Low-volume lines get their own marker. |
| `NotMeasured` | The cell content when `measured_status` is `not_measured` or a measured field is null: "not measured yet" in `--muted`. Never `0`, never blank. |
| `DataStamp` | `data_window`, `data_through`, `completeness`, `generated_at`, `git_sha` (short), `open_decisions`. A "partial window" banner when `is_window_complete` is false. |
| `Insight` | Renders `insight.text` verbatim; nothing when null. |
| `WorstIncident` | From `worst_incident`; `first_reason` quoted as TfL wrote it. |
| `TrendChart` | Build-time SVG over `lines/{id}.json` `history`, sized by attributes. Provisional weeks hatched, incomplete weeks outlined, a table fallback for screen readers. Needs at least four weeks to be worth drawing; until then show the table only. |
| `WeekHeatmap` | Line × week grid of Disruption Index (sequential scale on `--accent`, light to dark), provisional cells hatched, missing cells empty with "no data" in the accessible table. From v1 once there are four or more weeks. A line × day heatmap needs a contract change (section 6). |
| `BadHours` | Reuse `BarChart` with `typical_bad_hours`. |
| OG images | `BaseLayout` gains an optional `ogImage` prop (`{ path, width, height }`), so `/tube/` and `/tube/weeks/{w}/` use the week's 1200×675 card instead of the generated 1200×630 image. Line, archive and methodology pages keep the generated image (label `Tube league`). |
| Attribution | "Powered by TfL Open Data" in the `/tube/` footer notes, as TfL's open data terms require; no TfL roundel or logo. |

## 5. States the site must handle

- **Partial window** (`is_window_complete: false`, custom windows from `post_snapshot.py`): banner, and "partial" in the title.
- **Custom window** (`data_window.iso_week: null`): title from the dates ("4 to 10 November"), no movement column.
- **Provisional lines**: still ranked (D20), marked, reasons listed.
- **Not measured**: the measured columns say why, from `measured_withheld_reason` (see the contract changes for 0.4.0).
- **Open decisions**: listed on every data page while `open_decisions` is non-empty, with the methodology version line the cards use.
- **No complete week yet** (`latest_week: null`): `/tube/` says the first table is published on the first Monday after a full week of data.

## 6. Contract

### Already in the files

Stable slugs (`line_id`, `^[a-z-]+$`, used in every path), display names (`line_name`), TfL line colours (`colour`), `mode`, `is_low_volume`, ranks with movement, provisional flags with reasons, the measured view per line (filled when the line-week's gate passes, D42, D51), the worst incident, the insight line, line histories, typical bad hours, methodology weights and thresholds.

### Changed in this bundle

- **`meta/index.json`** (new, `contract/index.schema.json`): the site's entry point. `latest_week`, `weeks[]` (every complete week with a league file, newest first, with its path), `lines[]` (line_id, line_name, mode, colour, is_low_volume, path, sorted by name), `data_through`, `views`, `open_decisions`, and the common envelope.
- **Archive written every run** (D39): `export.py` now writes `league/{iso_week}.json` for every complete week with data, and `league/latest.json` is always the newest complete week. The archive, the line histories and the index therefore come from the same marts and code in the same run.
- **`schema_version` 0.2.0** (additive).

### Changed in the M6 and gate bundle (`schema_version` 0.3.0)

- **Disruption Index scale (D19, decided 2026-11-14).** It is now the time-weighted mean severity x 100, from 0 to 800. Values are about 1/(60 x the mean time weight) of the old ones, roughly a sixtieth to a seventieth (Bakerloo on 4 to 8 November: 141.5), and the schema caps the index at 800. Every archived week is rewritten on the next export (D39), so the site never mixes the two scales; show it with no thousands separator and one decimal at most. This is a change of meaning, so the minor version moves even though no field was removed (pre-1.0).
- **Unranked lines (D22).** Each standing and each line-history week has `unranked_reason`: `null` when ranked, `no_scored_minutes`, or `engineering_works` for a line that ran under half its scheduled hours. Show the latter as "Not ranked: engineering works" in place of the rank, as the card does. Standings also carry `engineering_works_hours`; history weeks carry it too.
- **`highlights`** in league files: `wooden_spoon` (line, rank, index, `is_provisional`, `shared_by`), `line_of_the_week` (line, index, previous index, improvement) and `line_of_the_week_null_reason` (`custom_window`, `no_previous_week`, `previous_week_incomplete`, `no_line_improved`). W46 has no Line of the Week; W47 is the first that can.
- **`monday_morning`**: the Monday Morning Index (07:00 to 10:00) per line for each Monday in the window, ranked, unranked lines last (D49).
- **`engineering_works`**: lines with hours closed for works in the window, most first (D50).
- **`worst_incident`** is now the most severity-weighted incident, not the longest, and carries `severity_weighted_minutes` (D48).
- **`measured.trains_run_share`**: trains on the boards over scheduled trains (ADR-014). Published from 0.4.0.
- **`meta/methodology.json`** gains `disruption_index` (definition, `min`, `max`), `thresholds.min_open_share_to_rank` and `settled_decisions`.
- The share card title is now "Which lines did TfL report as most disrupted?", and its subtitle gives the 0 to 800 scale.

### Changed for the measured view (`schema_version` 0.4.0, additive)

- **The measured view is published** (D42, settled 2026-11-14). `views.measured` is `published` when any line has measured numbers. Each standing, and each week in `lines/{line_id}.json` history, has `measured` filled or `null`, and a new `measured_withheld_reason`, `null` when measured:

  | Reason | Show |
  |---|---|
  | `line_not_measured` | "Not measured" (DLR, tram, Waterloo & City, Suffragette; D43) |
  | `shared_section_misfiled` | "Not measured: shared boards" (Circle, Hammersmith & City; D52) |
  | `no_measured_hours`, `gate_not_judged`, `gate_failed`, `too_few_measured_hours` | "Not enough reliable data this week" (D42, D51) |

- **`measured`** gains `trains_run_withheld_reason`, `measured_hours`, `measured_share`, `gate_pass_share`, `good_service_measured_hours` and `is_weak_evidence`. Show weak-evidence lines (the Elizabeth line and the Overground, D33) with a mark and a note "measured against estimated frequencies"; they have no `experienced_rank` (D53). Trains run is a lower bound ("trains shown on TfL's boards"), and can exceed 100% on weak-evidence lines.
- **`measured_status`** gains `withheld`: a measured line whose week did not qualify. `not_measured` now means only the four lines the measured view leaves out.
- **The Honesty Gap needs its caveat beside it** wherever it is shown, line pages included: "Long gaps are measured from TfL's arrival boards, which list about 85% of timetabled trains; if the rest ran, some gaps exist only on the boards." (methodology, section 3).
- **`insight.kind`** adds `trains_run`. The insight is the biggest Honesty Gap, then the lowest trains run, then the lowest Good Service % (D38).
- **`meta/methodology.json`** gains the gate thresholds (`gate_low_ratio`, `gate_high_ratio`, `gate_max_out_of_band_share`, `min_measured_share`), `measured_view_withheld_lines` and `trains_run_withheld_lines`.
- **`lines/{line_id}.json`** `measured_status` is `measured` when any week in the history has measured numbers.

### Changed in the rest of M6 and M7 (`schema_version` 0.5.0, additive)

- **`divisions/{yyyy-mm}.json`** (new, `contract/division.schema.json`). One file per month from November 2026.
  - `status` is one of:
    - `not_seeded`: fewer complete weeks than the seeding needs. There is no table, so show "Divisions start once two full weeks are in";
    - `seeded`: the seeding exists and the divisions start next month;
    - `pending`: a division month the build has not reached yet (just after midnight on the 1st). Show the previous month;
    - `in_progress` or `complete`.
  - `seeding` holds the seed order, its `status` (`provisional` until four complete weeks exist, ADR-015) and `first_division_month`.
  - `divisions[]` holds each division's lines with rank, `joined_by`, `movement` (`promoted`, `relegated`, `stays`) and `next_division`. While the month runs, `movement` is where the line stands, not a result.
- **`season/{season}.json`** (new, `contract/season.schema.json`): the season to date (1 April to 31 March), ranked like the weekly table. `data_window` spans the dates observed.
- **League files** gain three blocks:
  - `recovery_time`: `definition_status` is `provisional_d18`. Show it labelled provisional.
  - `industrial_action[]`: the actions touching the week, each with its source.
  - `strikes_excluded`: `is_same_as_main` and the variant table. It is `null` for a custom window.
- **`meta/methodology.json`** gains `divisions`, `season`, `recovery_time`, `industrial_action` (the whole seed, with sources) and `sensitivity` (weight sets, and per week Spearman's rho, max rank shift and top or bottom three changes).
- **`meta/index.json`** gains `months[]` (month, status, path) and `seasons[]`.
- **`open-data/{yyyy-mm}/manifest.json`** (new, `contract/open_data_manifest.schema.json`): the monthly open dataset's files with row counts and checksums, the attribution and the licence (D63). The site does not read it; see section 2.

### Still needed

| Gap | Needed for | Where |
|---|---|---|
| Mode display names (`tube` → "Tube", `elizabeth-line` → "Elizabeth line", `overground` → "London Overground") | Line page labels | Site-side map for now; add `mode_name` display text to `dim_lines` if more consumers appear |
| Per-day values per line in the league file (`days[]`: date, Good Service %, Disruption Index, scored minutes) | Line × day heatmap on week pages | M6 task: a `daily` block in `league/{iso_week}.json` from `fct_line_daily` (one more Athena query per run) |
| Card paths in `meta/index.json` | Linking each archived week to its card | After the R2 upload exists: the upload step knows which cards are in the bucket. Until then the site uses `cards/{iso_week}.png` for the latest week only |
| Post snapshots (custom windows) in the index | Archiving the Wednesday post's table | Optional: `post_snapshot.py` could upload to `snapshots/{start}_{end}/` with an index entry. v0 vendors it instead |
| A page for `open-data/` | `/tube/data/` | Once D63 is settled and the first month is uploaded |
| Public methodology prose | `/tube/methodology/` | M7 `docs/methodology.md` |

### How far back trends go

History starts at 2026-W45 (2 to 8 November). The collector began on 4 November, so W45 is partial and every line-week in it is provisional. The first full week is W46 (9 to 15 November), published Monday 16 November. Trend charts and the heatmap are worth drawing from about four complete weeks (W49, published 7 December); before that, line pages show the history as a table. `typical_bad_hours` is over the whole history (D37; a 12-week rolling window is the listed alternative).

## 7. Rollout

### v0: static snapshot, for the post on Wednesday 11 November

The post's window is Wednesday 4 to Tuesday 10 November (D36). The data reaches past it at Wednesday 00:00 London.

1. Wednesday morning, from a clean checkout of `main` after the bundle merges (so `git_sha` has no `-dirty`): `AWS_PROFILE=mtd-dev uv run python scripts/post_snapshot.py --start 2026-11-04 --end 2026-11-10 --out-dir ~/mtd-snapshots/2026-11-11`. Check `numbers.md` (runbook, "Snapshot for a post").
2. Owner decision before anything goes public: D36 (publish while D16 to D23 are open), plus D35 and D38 which shape what the card says.
3. In the website repo, vendor `league.json` and `card.png` into `src/data/tube/snapshot/` and build the v0 pages from them through the same data module (local source). Pages: `/projects/tube-league-table/` and `/tube/` (this window, marked as a custom window, with the provisional notes and open decisions), plus a short `/tube/methodology/` with the weights table from the repo's seeds or `TODO(Karsh)` prose. No line pages or archive yet: there is one partial week of history.
4. The post links to `karshvashi.com/tube/`, whose OG image is the card.

### v1: live weekly (after H12 and the M5 PR)

R2 bucket and domain live, `publish-weekly.yml` uploading on Mondays and calling the deploy hook. The site switches `/tube/` to the remote source, adds the archive, week pages and line pages, the nav item and the home block. First live table: W46 on Monday 16 November if H12 and M5 are done by then, otherwise the following Monday. The v0 snapshot stays as a fixture for dev and CI.

### v2: the measured view (after M4)

Measured columns in the league table (excess wait, long gaps per 100 hours, Honesty Gap), an "Experienced rank" toggle or second table, the Honesty Gap insight, and line pages with the declared vs measured comparison. The site already renders "not measured yet" in these slots, so v2 is a matter of filling them in and adding the comparison chart.

## 8. Tasks in the website repo

Carried out there by the owner, following that repo's contributor rules (its owner handles git; content rules 2.1 to 2.3 apply; checks: `npm run check`, `build`, `check:links`, `check:a11y`, `check:lighthouse`).

**v0 (for Wednesday 11 November)**

- [ ] 1. Add `src/lib/tube/types.ts` and zod schemas for the league contract fields the site uses (`schema_version` 0.x).
- [ ] 2. Add `src/lib/tube/data.ts` with the local source (reads `src/data/tube/snapshot/` or `TUBE_DATA_DIR`), validating every file and failing the build on error.
- [ ] 3. Vendor the Wednesday snapshot: `src/data/tube/snapshot/league.json` and `card.png`, with a comment naming the window, generating commit and `numbers.md` checked.
- [ ] 4. Build `LineChip`, `LeagueTable`, `RankMovement`, `ProvisionalMark`, `NotMeasured`, `DataStamp`, `Insight`, `WorstIncident` under `src/components/tube/`.
- [ ] 5. Add `src/pages/tube/index.astro` rendering the snapshot, with the custom-window, partial, provisional and not-measured states.
- [ ] 6. Add `src/pages/tube/methodology.astro` with the weights and thresholds tables and `TODO(Karsh)` prose.
- [ ] 7. Add `BaseLayout` `ogImage` prop and an endpoint that emits the card as `/og/tube/latest.png` (1200×675) for `/tube/`; add `/tube/methodology/` to `ogEntries`.
- [ ] 8. Write `src/content/projects/tube-league-table.mdx` (status `in-progress`, `featured: true`, stack from the repo, architecture `FlowDiagram` data in `src/data/diagrams/`), facts only from this repo's README and docs, `TODO(Karsh)` for prose that needs the owner's voice. `repo` only if the repo is public.
- [ ] 9. Add "Powered by TfL Open Data" to the `/tube/` notes.
- [ ] 10. Run all site checks; confirm the table passes axe and reads well at 375px in both themes.

**v1 (live weekly)**

- [ ] 11. Add the remote source to `data.ts`: base URL in `src/config.ts`, read `meta/index.json` first, cache-busting query, major-version check, fail the build on any error.
- [ ] 12. Endpoint route that copies `cards/{iso_week}.png` from R2 into `/og/tube/{iso_week}.png` at build time.
- [ ] 13. Add `src/pages/tube/weeks/index.astro` (archive) and `src/pages/tube/weeks/[week].astro` (`getStaticPaths` from `index.weeks`), with previous and next links.
- [ ] 14. Add `src/pages/tube/lines/[line].astro` (`getStaticPaths` from `index.lines`) with the history table, `BadHours` and worst week.
- [ ] 15. Build `TrendChart` and `WeekHeatmap` (build-time SVG, table fallback); show them from four complete weeks.
- [ ] 16. Add `Tube league` to `mainNav` and a "This week" block to the home page.
- [ ] 17. Point `/tube/` at `league/latest.json`; keep the v0 snapshot as the fixture CI builds against (`TUBE_DATA_DIR` in `ci.yml`), so PR builds do not depend on R2.
- [ ] 18. Document the data source, the deploy hook and the failure behaviour in `docs/hosting.md`.
- [ ] 19. After the first hook build, check `/tube/` matches `league/latest.json` and the OG image is the week's card.

**v2 (measured view, after M4)**

- [ ] 20. Fill the measured columns and add the Honesty Gap comparison to line pages; drop the "not measured yet" note once `views.measured` is `published`.

Total: 20 tasks (10 for v0, 9 for v1, 1 for v2).

## 9. Cloudflare setup for H12

What H12 ("Cloudflare: R2 bucket, API token, Pages deploy hook; add as GitHub secrets") needs for the site:

1. **R2 bucket** `mind-the-data-public` (location hint: Western Europe). Holds only published files; nothing private goes in it.
2. **Public access through a custom domain**: R2 → bucket → Settings → Custom Domains → `data.karshvashi.com` (the zone is already on Cloudflare). Leave the `r2.dev` URL off: it is rate-limited and meant for development.
3. **CORS policy** on the bucket:

   ```json
   [
     {
       "AllowedOrigins": ["*"],
       "AllowedMethods": ["GET", "HEAD"],
       "AllowedHeaders": [],
       "ExposeHeaders": ["ETag", "Content-Type"],
       "MaxAgeSeconds": 86400
     }
   ]
   ```

4. **API token**: R2 → Manage API tokens → Create → *Object Read & Write*, scoped to `mind-the-data-public` only. Note the access key id, secret and the account id (for the endpoint `https://<account_id>.r2.cloudflarestorage.com`).
5. **Pages deploy hook**: Workers & Pages → the `karsh-vashi-website` Pages project → Settings → Builds → Deploy hooks → add `mind-the-data weekly`, branch `main`. Treat the URL as a secret: anyone holding it can start builds.
6. **GitHub secrets** on the mind-the-data repo: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `PAGES_DEPLOY_HOOK_URL`. Never in code, logs or the website repo.
7. **Optional cache rule** for `data.karshvashi.com`: respect origin `Cache-Control` (the default); no Cache Everything override is needed.
8. **Check:** `curl -sI https://data.karshvashi.com/meta/index.json` returns 200 with the `Cache-Control` the upload set, and `Access-Control-Allow-Origin: *` when sent an `Origin` header.

The upload step in `publish-weekly.yml` (`src/mind_the_data/publish/upload.py`) sets `Content-Type` (`application/json; charset=utf-8`, `image/png`) and `Cache-Control` per object as in section 2, refuses any file outside this layout, uploads `league/latest.json` and `meta/index.json` last, and calls the hook only after every upload succeeded. A `dry_run` run uploads nothing and calls no hook; the files are kept as a workflow artifact. The exact owner steps, with commands, are in `docs/runbook.md` (H12).
