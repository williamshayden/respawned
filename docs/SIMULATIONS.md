# Pre-V1 use-case simulations

The simulator exercises the existing contact-based engine with synthetic source
records, real PostgreSQL commits, the actual HTTP ingestion handler, and the CLI
review handler. Scripted model responses and reviewer actions make the scenarios
repeatable without a provider, credentials for a model, or any sending.

Eight scenarios ran successfully on September 8, 2026 against PostgreSQL 16.15.
The initial run demonstrated six supported workflows and two product limits.
The current runner also checks the newly added reply inbox; it still documents
the application-tracking gap explicitly.

| Story | Demonstrated outcome |
| --- | --- |
| Avery replies about two projects | One grouped candidate; skip preserves the draft; edit and simulated approval reserve the exact copy; CSV matches; a new reply cannot bypass the reservation cooldown. |
| A client revisits a proposal on different dates | Repeat-view signal; grouping follows stable contact identity, not a shared inbox; rejected evidence stays suppressed until a new reply arrives. |
| A connector retries contradictory data | Exact replay adds no activities; conflicting event identity or an orphan event returns 409 and rolls back the whole batch. |
| An opportunity closes while reviewing | Approval rechecks the ingested closure and creates no outbox reservation. |
| The recipient changes while reviewing | The displayed draft cannot be approved; refresh produces a draft for the corrected address. |
| The model times out or returns placeholders/currency | No draft or outbox row persists; a valid retry succeeds. |
| A customer replies immediately after our message | The new reply inbox shows the reply immediately. The outbound follow-up queue remains suppressed until the 72-hour boundary. |
| Two job applications receive automated receipts | No-route import returns 422 even with a contact key. With a known recruiter, custom receipt events are stored without becoming human replies. Generic aging later groups the two roles by recruiter; drafting receives no role or correspondence context. |

The first story is a useful walkthrough: import two opportunities for Avery,
record an outbound message four days ago and Avery's reply one hour ago, refresh
the queue, skip the generated draft, reopen it, edit it, and approve in the
simulation. The output contains the screen shown before each scripted action,
the exact edited message, and the unsent CSV reservation.

The initial cooldown result led to a read-only reply-needed inbox, now available
through `inbox --json` and `GET /v1/inbox`. Reply visibility is independent of the
outreach throttle; approval and cooldown rules remain enforced. Similarly,
`replied_no_answer` means we owe the contact a response. It does not mean we are
waiting for an employer or label to answer a submission.

## Run locally

From a development checkout, install the locked dependencies and set
`SIMULATION_POSTGRES_URL` to an existing disposable/test PostgreSQL database.
The role needs `CREATE SCHEMA` permission. The runner creates a unique schema per
scenario, uses only that schema, saves evidence, and drops only its own schema.
It does not initialize, stop, or recreate any existing service.

```bash
uv sync --frozen
export SIMULATION_POSTGRES_URL='postgresql+psycopg2://user:password@localhost/test_database'
uv run --frozen python scripts/simulate_use_cases.py --output exports/simulations/run-1
```

In PowerShell, set the variable with
`$env:SIMULATION_POSTGRES_URL = 'postgresql+psycopg2://user:password@localhost/test_database'`
and run the same `uv run` command. Use a new output directory for each run; the
runner refuses to overwrite an existing directory. Generated exports are ignored
by Git. There are no machine-specific paths in the simulator.

Open `REPORT.md` in that output directory. `results.json` contains scenario and
assertion results. Each scenario has `evidence.json` with source requests,
responses, drafting prompts, and final database rows, plus `review.txt` containing
the CLI display. The customer-reply scenario also exports `outbox.csv`.

## What this does and does not establish

The HTTP requests run through FastAPI's in-process test client; they do not use a
listening server. The CLI review handler runs with scripted inputs, so the
simulation is not evidence of actual human authorization or shell command
parsing. Source classifications are supplied by the fixture, not inferred by an
agent. Automated receipts are ignored only because the source labels them with
a custom event type; this does not test automatic message classification.

No live model, Gmail, SMS, delivery worker, Docker lifecycle, concurrent-reviewer
load, or transaction commit-failure response is exercised. Raising a timeout in
the scripted model verifies recovery from that error, not an actual provider's
timeout duration. It does not evaluate natural-language quality or prove that
the bounded prompt contains enough context for a useful real response.

The PostgreSQL 16 volume target, Docker LICENSE copy, and commit-before-response
handling have been corrected. Focused checks cover transaction response order
and installation from the Dockerfile's copied files. Full Docker lifecycle and
backup/restore verification remain outstanding; see [release checks](RELEASE_CHECKS.md).
A passing simulation is additional behavioral evidence, not a release-readiness
sign-off. See [agent simulations](AGENT_SIMULATIONS.md) for live agent decisions.
