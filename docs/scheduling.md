# Recurring monitor scheduling

While the API is running, active monitors automatically queue checks at their
saved `check_interval_seconds`. A separate scheduling loop polls persisted
`next_run_at` values every five seconds, independently of model requests,
alert emails, and the administrator automatic-processing switch.

## Scheduling and processing

- Creating a monitor queues its initial check and sets the first future due time.
- At a due time, the scheduler queues one `scheduled` run. It saves the intended
  due time in `scheduled_for` and uses a slot-specific idempotency key.
- If any check for that monitor is already queued or running, the due check
  coalesces with it. It does not accumulate extra research runs.
- Whether a new run was queued or existing work was coalesced, the next due time
  becomes the poll time plus the configured interval.
- With automatic queue processing off, checks remain queued until a manual step
  or explicit CLI one-shot processes them. Turning processing off does not stop
  scheduling. Pause the monitor to stop its automatic checks.
- A pending composition job can delay research execution; the scheduler can still
  leave one research check waiting. Scheduler activity makes no model calls.

Only active, undeleted monitors belonging to verified, non-banned accounts are
eligible. Paused/archived/deleted monitors do not generate recurring work.
Pausing cancels queued initial/scheduled checks, and manual checks remain allowed
for paused monitors. Reactivation queues one immediate check and resets the future
due time. Changing the interval on an active monitor resets its due time from the
edit time. Manual checks do not reset the automatic timer.

## Downtime, timing, and concurrency

Due times survive API restarts. When the API returns, each overdue eligible
monitor gets one catch-up check, coalesced if work is already waiting. The scheduler
does not replay every interval missed during downtime. Failed research remains
historical; a later scheduled run is a new check, not a retry of the failed run.

The interval controls when work is queued, not when research finishes. Polling,
queue backlog, long-running research, and composition can delay execution. Intervals
shorter than five seconds are subject to the poll interval. These are elapsed-time
intervals rather than timezone-aware calendar schedules.

Each tick handles at most 100 due monitors, oldest first. Large batches may span
several polls. Monitor row locks with `SKIP LOCKED` coordinate API replicas and
manual checks; the existing unique in-flight-run index also prevents overlap.
Enqueueing and timer advancement commit together. Database failures roll back the
tick and are retried on later polls. Scheduling leaves configuration versions and
`updated_at` unchanged, so advancing a timer does not invalidate active research.

## Deployment and inspection

No new migration, cron service, or environment setting is required. Deploy the
updated backend image using the [server update procedure](deployment/deployment.md).
An already-running older image will not acquire the scheduler until updated.
The optional standalone research CLI consumes work but does not host this scheduler;
the API must remain running for recurring enqueueing.

Use **Administration → Monitor runs** and filter by the scheduled trigger to inspect
queued/executed work. Monitor detail exposes the next due time, and the run API
exposes `scheduled_for`. The queue heartbeat measures the processing service;
it is not a separate scheduler heartbeat. Scheduler failures appear in API logs
as `Monitor scheduler unavailable` with a safe exception type.

The existing [worker](monitor-worker.md), [alert](alerts.md), and
[notification](notifications.md) pipelines handle scheduled runs just like other
checks. A scheduled check can complete successfully without producing an alert.
