# Resume checkpoint

## Working now

- Directional shipment → invoice and invoice/payment → warehouse flows with field ownership enforced by allowlists.
- Three persistent SQLite stores: independent warehouse/accounting mocks and integration journal.
- Duplicate protection at inbox and destination business-key boundaries.
- Strict validation, quarantine, retained reused-event conflicts, three-attempt dead letters, audit output.
- Recovery after an invoice commits but the response times out, including reopen/restart test.
- Status versions prevent stale payment regression; conflicting equal versions are quarantined.
- Reconciliation compares actual business facts and status mirrors; unresolved events block health.
- CLI happy path and intentional failure demo; GitHub Actions Windows/Linux Python 3.11/3.12 matrix.

## Test status

20 tests pass locally on Python 3.12.14. Full failure demo and healthy payment flow also verified locally. Check the GitHub Actions tab for hosted results after publication.

## Incomplete and known boundaries

No known failing tests at this checkpoint. This is not production-ready. There is no operator resolution workflow, automatic retry scheduler, worker heartbeat, concurrent lease, real API adapter, inventory balance sync, monetary calculation, schema migration, or alerting. Source shipments are assumed immutable after invoicing. The same event ID with changed payload creates a durable conflict that cannot yet be acknowledged through the CLI. Polling scans all records and captures current state rather than every intermediate transition.

Integration state changes and destination writes are deliberately not one transaction. Replay relies on destination idempotency. Unexpected SQLite/programming errors surface rather than being swallowed. Multiple workers are unsupported; do not infer concurrency guarantees from the single-worker tests.

## Recommended next improvements, in order

1. **Operator recovery workflow.** Add inspect/resolve commands for quarantine, conflict, and dead-letter records. Preserve rejected history; require an explicit corrective decision and a new immutable event. Test that invalid release is rejected and resolved health is truthful. Do not add a generic clear-errors command.
2. **Timed retry and freshness.** Persist `next_attempt_at`, add bounded exponential backoff with jitter, and track worker last-success/last-seen source timestamps. Test due-time selection and stale-worker detection without real sleeps.
3. **One real destination adapter.** Define a narrow interface and add an HTTP accounting sandbox connector with timeouts, classification of permanent/temporary errors, pagination, and contract tests proving server-side idempotency after ambiguous timeout.
4. **Atomic worker claim and migrations.** Introduce leases only when multiple workers are needed, migrate the three schemas explicitly, and prove contention/crash recovery before claiming concurrent safety.
5. **Expand business fidelity.** Model multi-line shipments, pricing/decimal money, split invoices, credit notes, returns, and inventory reconciliation with explicit ownership and finance-approved rules.

## Exact resumption commands

```sh
git clone https://github.com/bruceatom/reliable-two-way-system-sync.git
cd reliable-two-way-system-sync
git status --short
python -m unittest discover -s tests -v
python -m src.cli --data resume-demo demo
python -m src.cli --data resume-demo audit
python -m src.cli --data resume-demo reconcile
```

The last command should exit 1 for the deliberately rejected shipment. Use a new data directory if `resume-demo` already exists. No package installation is needed. Read `src/sync.py`, `src/idempotency.py`, and the tests before implementing recovery; preserve the timeout-after-commit and ownership tests.

For healthy flow:

```sh
python -m src.cli --data resume-happy seed SHP-001
python -m src.cli --data resume-happy sync
python -m src.cli --data resume-happy pay SHP-001
python -m src.cli --data resume-happy sync
python -m src.cli --data resume-happy reconcile
```

Repository publication should be verified by comparing local `HEAD` with `origin/main` and checking a clean `git status --short`. No generated databases or credentials belong in Git.
