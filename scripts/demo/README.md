# Recorded agent and connector demo

The recording uses mock application correspondence and a live Codex backend.
`scripts/demo/serve.py` owns an isolated database schema. `agent.py` reads the
correspondence, imports source facts through HTTP, and evaluates the queue.
The operator reviews the draft in the UI and explicitly approves it.

After that approval, record the connector with a new output directory:

```sh
RESPAWNED_OUTBOX_TOKEN=respawned-live-demo-outbox \
uv run python scripts/demo/deliver.py \
  --url http://127.0.0.1:8127 \
  --output /tmp/respawned-test-delivery-take-1
```

The token above belongs to `serve.py`'s disposable demo engine. Run the script
in the same app environment so `respawned outbox --pending --json` uses the
current CLI. The script needs no database configuration or operator token.

`deliver.py` refuses any pending batch except the single human-approved
`demo:application:northstar-backend` message to `maya@northstar.example`.
It posts the exact recipient, channel, body, and outbox ID to an ephemeral
loopback **TEST delivery** endpoint. The outbox ID is its idempotency key.
That endpoint only writes durable JSON; no email or messaging provider runs.
After confirmed acceptance, the connector records the result through
`POST /v1/outbox/{id}/receipt` and checks identical endpoint and receipt replays.
Respawned then reports the message as sent based on that TEST confirmation.

The output contains the actual CLI stdout before and after, the approved
snapshot, the test endpoint's durable record, `receipt.json`, the final outbox
detail, and `trace.json`. Keep the TEST delivery label in footage and captions.
If a request fails, inspect these files before continuing; the script does not
retry failed requests or resume an existing output directory.
