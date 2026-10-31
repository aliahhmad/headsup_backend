# HeadsUp documentation

HeadsUp researches topics described by users, retains sourced observations and
monitor knowledge, and publishes meaningful changes as alerts. This documentation
covers the current backend and its connected React application.

## Getting started

1. Follow the [local quick start](quick-start.md) to start the database, email inbox,
   API, and frontend, then create an administrator.
2. Create a monitor and use [queue controls](queue.md) to process its initial check.
3. Inspect its findings and [alerts](alerts.md), or use the
   [run dashboard](run-dashboard.md) to examine the complete pipeline.
4. For a server, use the [deployment runbook](deployment/deployment.md).

## Feature guides

| Area | Guide |
| --- | --- |
| Monitor ownership, lifecycle, and checks | [Monitors](monitors.md) |
| Intervals, catch-up, and queued scheduled checks | [Recurring scheduling](scheduling.md) |
| Research, evidence, and knowledge updates | [Research worker](monitor-worker.md) |
| Decisions, deduplication, and final updates | [Alerts](alerts.md) |
| Bell and inbox events | [In-app notifications](notifications.md) |
| Profile opt-in, SMTP, and delivery recovery | [Alert emails](alert-emails.md) |
| Administrator processing controls | [Queue management](queue.md) |
| Run context, memory, evidence, and request traces | [Run dashboard](run-dashboard.md) |
| Registration, verification, and administration | [Accounts](accounts.md) |

## Operations and development

- [Configuration](usage/configuration/environment.md)
- [Database and service operations](operations.md)
- [Development and testing](development/local.md)
- [Documentation maintenance](development/documentation.md)
- [Project structure](project-organization.md)
- [API reference](reference/api.md)
- [Troubleshooting](troubleshooting.md)
- [Deferred capabilities](future.md)

Active monitors automatically queue checks at their intervals while the API is
running. Automatic queue processing defaults to off in a new database and resumes
its saved setting after restarts. Scheduling continues while processing is paused;
checks wait in the queue. Chat processing is not implemented.
