# Product demo

[![Product walkthrough preview](media/respawned-demo-poster.png)](media/respawned-demo.mp4)

| Video | Format |
| --- | --- |
| [Product walkthrough](media/respawned-demo.mp4) | 48.56 seconds, 1920 × 1080, 25 fps, silent |
| [Review loop](media/respawned-review-loop.mp4) | 14.24 seconds, 1116 × 1048, 25 fps, silent |

The walkthrough shows configurable workspaces, record context, drafting, saved
edits, human approval, and CSV export. It uses real browser footage of the
bundled app at commit `7e44fb42de8db356c585ab072297edad2a68109f`, with sample
records and deterministic model responses from a local stub. Its CLI export
transcript came from an actual command; that CSV matched the UI export byte for
byte. Approval left the message unsent.

The agent-integration segment is an illustrative code example. It does not
claim an autonomous agent or mailbox connector ran. Use the
[agent integration guide](AGENT_INTEGRATION.md) to connect your own tooling.

Captions, transitions, and framing were added during editing. Timings do not
measure model or network latency. The review loop returns to its first frame
through an edit; the application does not undo the saved draft. No audio, live
model provider, or message delivery is represented. The
[media provenance](media/demo-provenance.json) records the source revision,
recording/edit details, and file metadata. The exported videos are versioned
here; the original capture/edit workspace is not included in the repository.

## Run the workflow locally

The repository's connected simulation provides synthetic records, the real API
and PostgreSQL, and stubbed drafting for a fresh review session or recording.
From a developer checkout with dependencies installed, supply a loopback test
database whose role can create schemas:

```bash
uv run python scripts/serve_ui_simulation.py \
  --postgres-url 'postgresql+psycopg2://user:password@127.0.0.1:5432/test_database' \
  --port 8001
```

Follow the [simulation's browser access instructions](WEB_UI.md#run-the-connected-simulation),
then inspect a record, generate and edit its draft, save, approve, and export the
unsent outbox. The harness creates its own schema and removes it on normal
shutdown. Restart for a fresh session; `--empty` starts without sample records.

This repository simulation reproduces the application workflow with its own
versioned fixtures. It does not regenerate the same edited video or exact demo
dataset. No external source connection or model account is needed.
