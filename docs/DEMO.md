# Product demo

[![Product walkthrough](media/respawned-demo-poster.png)](media/respawned-demo.mp4)

The walkthrough follows one mock application through a real Codex backend and the Respawned API:

1. Codex reads the correspondence, imports confirmed facts, and evaluates the queue.
2. The reviewer opens the source message, edits the generated draft, and approves it in the UI.
3. `respawned outbox --pending --json` returns the exact approved message.
4. A local test sender accepts it. The connector records a receipt, and the UI shows **Sent**.

The test sender writes one local JSON file. No email or messaging provider is contacted. Replaying the test delivery and receipt creates no duplicate.

The video uses recorded browser actions and typeset excerpts of actual CLI/API output. It is silent; timing and transitions are edited for readability, not latency measurements. [Media provenance](media/demo-provenance.json) records the evidence and file hashes. Raw model events and local credentials are excluded.

Use the [demo scripts](../scripts/demo/README.md) to reproduce the workflow with a disposable PostgreSQL schema and an authenticated Codex runtime. For your own source or sender, use the [agent guide](AGENT_INTEGRATION.md), [pasteable prompt](agent-prompt.txt), and [outbox API](API.md#outbox-integration).
