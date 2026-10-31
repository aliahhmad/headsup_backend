# Deferred capabilities

These capabilities are not currently implemented:

- Conversational chat processing, chat summarization, and model-driven updates to
  monitor context or personal memory.
- Push delivery, digests across runs, notification batching, quiet hours, and
  user-defined notification thresholds.
- Configurable historical observation selection, token budgets, and tighter
  cost controls beyond the current schema validation limits.
- Automatic recovery/retry of research runs left running after a hard process kill.
- Exact monetary cost reporting and complete fetched-page archival.

Current features include monitor ownership/lifecycle, recurring interval scheduling,
sourced research, knowledge
revisions, decisions and conditional alert composition, expandable update history,
in-app notifications, opt-in alert email, queue administration, and run inspection.

The [alert guide](alerts.md#observation-and-request-size-budgets) records proposed
cost controls explicitly as proposals. The [run dashboard guide](run-dashboard.md#useful-next-additions)
lists possible observability improvements. Neither list is a release commitment.
