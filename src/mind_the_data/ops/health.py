"""Collector health for the scheduled ops workflows and ``make status``.

Each check returns a :class:`Section`: lines of Markdown plus the problems it
found. A check that cannot read what it needs reports that as a problem too,
so a permission gap shows up as a red report rather than a quiet green one.
"""

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from botocore.exceptions import BotoCoreError, ClientError

if TYPE_CHECKING:
    from mypy_boto3_cloudwatch import CloudWatchClient
    from mypy_boto3_s3 import S3Client

    from mind_the_data.publish.mechanics import IndustrialAction

ALARM_PREFIX = "mind-the-data-"
LAMBDA_FUNCTIONS = ("mind-the-data-poller", "mind-the-data-reference")
LIVE_FEEDS = ("line_status", "line_disruption", "arrivals", "line_arrivals", "stop_disruption")
REFERENCE_FEEDS = ("ref_route_sequence", "ref_timetable", "ref_severity")
# Polled every minute, every fifth minute, or daily; older than this is stale.
MAX_AGE = {
    "line_status": timedelta(minutes=10),
    "line_disruption": timedelta(minutes=10),
    "arrivals": timedelta(minutes=10),
    "line_arrivals": timedelta(minutes=10),
    "stop_disruption": timedelta(minutes=20),
    # Daily since M8, with a day's slack for a failed run and its retries.
    "ref_route_sequence": timedelta(days=2),
    "ref_timetable": timedelta(days=2),
    "ref_severity": timedelta(days=2),
}
AWS_ERRORS = (BotoCoreError, ClientError)
ALWAYS_RUNNING = frozenset({"mind-the-data-poller"})
STUCK_INSUFFICIENT = timedelta(hours=1)


@dataclass
class Section:
    title: str
    lines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    # Set when the section could not be built at all (no access).
    unavailable: bool = False

    def markdown(self) -> str:
        body = "\n".join(self.lines) if self.lines else "_nothing to report_"
        return f"### {self.title}\n\n{body}\n"


def guarded(title: str, check: Callable[[], Section]) -> Section:
    """Run ``check``; an AWS error becomes a problem in its section."""
    try:
        return check()
    except AWS_ERRORS as exc:
        reason = f"could not read: {type(exc).__name__}: {exc}"
        return Section(title, [reason], [f"{title}: {reason}"])


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(), tzinfo=UTC)
    return start, start + timedelta(days=1)


# ---- completeness ------------------------------------------------------------


def completeness_section(raw_json: str) -> Section:
    """Summarise ``scripts/completeness.py --json`` output."""
    section = Section("Completeness")
    try:
        report = json.loads(raw_json)
        window = f"Window {report['window_start']} to {report['window_end']} UTC.\n"
        feeds: dict[str, Any] = dict(report["feeds"])
        if not feeds:
            raise ValueError("no feeds")
    except (ValueError, KeyError, TypeError):
        section.lines.append("The completeness report did not produce a result.")
        section.problems.append("completeness: report did not run (see the job log)")
        return section
    section.lines.append(window)
    section.lines.append("| feed | minutes covered | coverage | target | request success |")
    section.lines.append("| --- | --- | --- | --- | --- |")
    for name, feed in feeds.items():
        ok = feed["coverage"] >= feed["target"]
        section.lines.append(
            f"| {name} | {feed['covered']}/{feed['minutes']} | {feed['coverage']:.2%} "
            f"| {feed['target']:.0%} | {feed['request_success']:.2%} |"
        )
        if not ok:
            gaps = feed.get("gaps", [])
            shown = ", ".join(f"{a[11:16]}-{b[11:16]}" for a, b in gaps[:5])
            more = f" and {len(gaps) - 5} more" if len(gaps) > 5 else ""
            section.problems.append(
                f"completeness: {name} {feed['coverage']:.2%} is below its "
                f"{feed['target']:.0%} target (gaps {shown}{more})"
            )
    return section


# ---- alarms --------------------------------------------------------------------


@dataclass(frozen=True)
class Alarm:
    name: str
    state: str
    since: datetime | None = None


def current_alarms(cloudwatch: "CloudWatchClient", prefix: str = ALARM_PREFIX) -> list[Alarm]:
    alarms: list[Alarm] = []
    paginator = cloudwatch.get_paginator("describe_alarms")
    for page in paginator.paginate(AlarmNamePrefix=prefix, AlarmTypes=["MetricAlarm"]):
        for a in page.get("MetricAlarms", []):
            alarms.append(Alarm(a["AlarmName"], a["StateValue"], a.get("StateUpdatedTimestamp")))
    return sorted(alarms, key=lambda a: a.name)


def alarms_fired(
    cloudwatch: "CloudWatchClient", start: datetime, end: datetime, prefix: str = ALARM_PREFIX
) -> dict[str, int]:
    """How many times each alarm went into ALARM in [start, end)."""
    fired: dict[str, int] = {}
    paginator = cloudwatch.get_paginator("describe_alarm_history")
    pages = paginator.paginate(HistoryItemType="StateUpdate", StartDate=start, EndDate=end)
    for page in pages:
        for item in page.get("AlarmHistoryItems", []):
            name = item.get("AlarmName", "")
            if name.startswith(prefix) and _went_to_alarm(item.get("HistoryData", "")):
                fired[name] = fired.get(name, 0) + 1
    return fired


def _went_to_alarm(history_data: str) -> bool:
    try:
        data = json.loads(history_data)
    except ValueError:
        return False
    return bool(data.get("newState", {}).get("stateValue") == "ALARM")


def alarm_section(
    alarms: Sequence[Alarm], fired: dict[str, int] | None = None, now: datetime | None = None
) -> Section:
    section = Section("Alarms")
    if not alarms:
        section.lines.append(f"No alarms named `{ALARM_PREFIX}*` were found.")
        section.problems.append("alarms: none found (are they deployed?)")
        return section
    section.lines.append("| alarm | state now | went into ALARM |")
    section.lines.append("| --- | --- | --- |")
    for alarm in alarms:
        count = fired.get(alarm.name, 0) if fired is not None else 0
        times = "-" if fired is None else f"{count}x" if count else "no"
        section.lines.append(f"| {alarm.name} | {alarm.state} | {times} |")
        if alarm.state == "ALARM":
            section.problems.append(f"alarms: {alarm.name} is in ALARM")
        elif count:
            section.problems.append(f"alarms: {alarm.name} went into ALARM {count}x")
        # Every project alarm treats missing data explicitly, so one that stays
        # in INSUFFICIENT_DATA is evaluating a metric that no longer exists.
        if (
            alarm.state == "INSUFFICIENT_DATA"
            and now is not None
            and alarm.since is not None
            and now - alarm.since > STUCK_INSUFFICIENT
        ):
            section.problems.append(
                f"alarms: {alarm.name} has had insufficient data since {alarm.since:%Y-%m-%d %H:%M}"
            )
    return section


# ---- Lambda errors ---------------------------------------------------------------


def lambda_metric_sum(
    cloudwatch: "CloudWatchClient", function: str, metric: str, start: datetime, end: datetime
) -> int:
    response = cloudwatch.get_metric_statistics(
        Namespace="AWS/Lambda",
        MetricName=metric,
        Dimensions=[{"Name": "FunctionName", "Value": function}],
        StartTime=start,
        EndTime=end,
        Period=int((end - start).total_seconds()),
        Statistics=["Sum"],
    )
    return int(sum(p.get("Sum", 0.0) for p in response.get("Datapoints", [])))


def lambda_section(
    cloudwatch: "CloudWatchClient",
    start: datetime,
    end: datetime,
    functions: Iterable[str] = LAMBDA_FUNCTIONS,
) -> Section:
    section = Section("Lambda errors")
    section.lines.append("| function | invocations | errors | throttles |")
    section.lines.append("| --- | --- | --- | --- |")
    for function in functions:
        counts = [
            lambda_metric_sum(cloudwatch, function, metric, start, end)
            for metric in ("Invocations", "Errors", "Throttles")
        ]
        section.lines.append(f"| {function} | {counts[0]} | {counts[1]} | {counts[2]} |")
        if counts[1]:
            section.problems.append(f"lambda: {function} had {counts[1]} errors")
        if counts[2]:
            section.problems.append(f"lambda: {function} was throttled {counts[2]}x")
        # The poller runs every minute; no invocations means its schedule stopped.
        if function in ALWAYS_RUNNING and not counts[0]:
            section.problems.append(f"lambda: {function} was never invoked")
    return section


# ---- raw freshness ---------------------------------------------------------------


def _last_prefix(s3: "S3Client", bucket: str, prefix: str) -> str | None:
    """The greatest common prefix one level below ``prefix`` (dt=... or hour=...)."""
    found: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
        found.extend(p["Prefix"] for p in page.get("CommonPrefixes", []))
    return max(found) if found else None


def newest_object(s3: "S3Client", bucket: str, feed: str) -> datetime | None:
    """LastModified of the newest raw object for ``feed``.

    Walks the newest dt= then hour= partition, so it costs three listings
    whatever the bucket's size.
    """
    day = _last_prefix(s3, bucket, f"raw/{feed}/")
    if day is None:
        return None
    hour = _last_prefix(s3, bucket, day)
    newest: datetime | None = None
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=hour or day):
        for obj in page.get("Contents", []):
            modified = obj["LastModified"]
            if newest is None or modified > newest:
                newest = modified
    return newest


def format_age(age: timedelta) -> str:
    seconds = int(age.total_seconds())
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 7200:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def freshness_section(
    s3: "S3Client",
    bucket: str,
    now: datetime,
    feeds: Iterable[str] = (*LIVE_FEEDS, *REFERENCE_FEEDS),
) -> Section:
    section = Section("Newest raw object per feed")
    section.lines.append("| feed | newest | age | limit |")
    section.lines.append("| --- | --- | --- | --- |")
    for feed in feeds:
        newest = newest_object(s3, bucket, feed)
        limit = MAX_AGE[feed]
        if newest is None:
            section.lines.append(f"| {feed} | none | - | {format_age(limit)} |")
            section.problems.append(f"raw: no {feed} objects at all")
            continue
        age = now - newest
        section.lines.append(
            f"| {feed} | {newest:%Y-%m-%d %H:%M} UTC | {format_age(age)} | {format_age(limit)} |"
        )
        if age > limit:
            section.problems.append(f"raw: newest {feed} object is {format_age(age)} old")
    return section


# ---- industrial action seed ----------------------------------------------------

# Days after an announced action's last day before the seed counts as stale;
# the same grace as dbt's assert_announced_industrial_action_is_current.
INDUSTRIAL_ACTION_GRACE_DAYS = 3


def industrial_action_section(actions: Sequence["IndustrialAction"], today: date) -> Section:
    """Announced actions that have passed and still need `held` or `called_off` (D60).

    The dbt test only warns, so a missed hand update cannot stop the nightly
    build; this puts it in the daily report as a problem instead. `today` is
    the report's UTC date; dbt uses the London date, so the two can differ
    by a day around midnight, which the three days' grace absorbs.
    """
    title = "Industrial action seed"
    stale = sorted(
        (
            a
            for a in actions
            if a.status == "announced"
            and a.end_london_date < today - timedelta(days=INDUSTRIAL_ACTION_GRACE_DAYS)
        ),
        key=lambda a: (a.end_london_date, a.action_id),
    )
    if not stale:
        return Section(title, ["No announced action is past its last day plus the grace."])
    lines = [
        f"- `{a.action_id}` ended {a.end_london_date:%Y-%m-%d}: set `status` to `held` or "
        "`called_off` in `transform/seeds/seed_industrial_action.csv` (runbook, "
        '"Industrial action seed")'
        for a in stale
    ]
    return Section(title, lines, [f"{title}: {len(stale)} announced action(s) out of date"])


def industrial_action_report(
    load: Callable[[], Sequence["IndustrialAction"]], today: date
) -> Section:
    """`industrial_action_section` over the seed, with a bad seed as a problem, not a crash."""
    try:
        return industrial_action_section(load(), today)
    except (OSError, ValueError, KeyError) as exc:
        reason = f"could not read the seed: {type(exc).__name__}: {exc}"
        return Section("Industrial action seed", [reason], [f"Industrial action seed: {reason}"])


# ---- report ------------------------------------------------------------------------


def render(title: str, sections: Sequence[Section], footer: str = "") -> str:
    problems = [p for s in sections for p in s.problems]
    verdict = (
        "**All targets met.**"
        if not problems
        else "**Missed:**\n\n" + "\n".join(f"- {p}" for p in problems)
    )
    parts = [f"## {title}\n", verdict + "\n", *(s.markdown() for s in sections)]
    if footer:
        parts.append(footer + "\n")
    return "\n".join(parts)
