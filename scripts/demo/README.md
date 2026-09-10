# Recorded agent and connector demo

Run these scripts from a developer checkout with dependencies installed, a loopback test PostgreSQL database, and an authenticated Codex runtime. The scripts use mock correspondence and make real Codex calls.

Start the engine with a new output directory:

```sh
uv run python scripts/demo/serve.py \
  --postgres-url 'postgresql+psycopg2://user:password@127.0.0.1:5432/test_database' \
  --codex-bin /absolute/path/to/codex \
  --scratch-dir /absolute/path/to/existing/scratch \
  --output /tmp/respawned-take-1 --port 8127
```

The harness owns one temporary schema and removes it on normal shutdown. Windows Codex launched from WSL needs its executable and scratch directory on a mounted Windows drive. The generated `cli-env.sh` contains API connection settings; database settings stay on the engine.

In another terminal, import the mock correspondence with the agent:

```sh
. /tmp/respawned-take-1/cli-env.sh
uv run python scripts/demo/agent.py \
  --codex-bin /absolute/path/to/codex \
  --scratch-dir /absolute/path/to/existing/scratch \
  --output /tmp/respawned-take-1/agent
```

Open the printed engine URL and enter the demo-only token `respawned-live-demo-review` under **Setup → Engine access**. Open **Review queue**, generate a draft, inspect the source message, edit and save the text, then choose **Approve to outbox**.

Record the connector after approval:

```sh
RESPAWNED_OUTBOX_TOKEN=respawned-live-demo-outbox \
uv run python scripts/demo/deliver.py \
  --url http://127.0.0.1:8127 \
  --output /tmp/respawned-take-1/delivery
```

`deliver.py` accepts only the single human-approved Northstar demo message. It reads the actual CLI and API outbox, then posts the exact message to an ephemeral loopback **TEST delivery** endpoint. The endpoint writes one durable JSON file; no email or messaging provider runs. After confirmed acceptance, the connector records a receipt and checks both endpoint and receipt replays. Respawned then shows **Sent** based on that test confirmation.

The delivery script needs no database configuration or operator token. Its output includes CLI stdout, the approved snapshot, the durable test record, receipt, final outbox detail, and trace. Keep the TEST label in footage. Inspect saved evidence after any failure; the script does not retry failed requests or resume an existing output directory.
