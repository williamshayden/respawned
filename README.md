# Follow-up Engine

Follow-up Engine is an MVP for prioritizing open service quotes, drafting professional follow-up messages with an LLM, and placing human-approved messages into a mock delivery outbox. It loads quote and event data into PostgreSQL, reduces each quote to its current state, applies a deterministic follow-up policy, and presents the highest-value customers for review.

## Quickstart

### Prerequisites

- Docker with Compose v2
- [uv](https://docs.astral.sh/uv/)
- An Anthropic API key, other models/providers/keys can be configured via litellm proxy
- Python 3.12 or newer

### Tested platform

This MVP was built and tested on Windows using WSL2, with Docker Desktop's WSL integration and uv running inside WSL. Docker and uv keep the application largely platform-independent.

Native Windows, macOS, other Linux environments, and production deployment targets have not been qualified as thoroughly. Differences in container networking, bind mounts, filesystem permissions, environment loading, and service lifecycle may require deployment-specific adjustments. 

### 1. Configure the environment

Copy the development template from the repository root:

```bash
cp .env.example .env
```

In PowerShell, use `Copy-Item .env.example .env`. The template contains host-facing development values and fake secrets. It works as-is for loading and scoring; replace `ANTHROPIC_API_KEY` before live drafting. Compose overrides host addresses inside the application container with Docker service names such as `db` and `litellm`. If you change `LITELLM_PROXY_PORT`, update `LITELLM_PROXY_URL` to match it.

### 2. Install the project and start PostgreSQL

```bash
uv sync --frozen
docker compose --profile postgres up -d --wait db
```

This starts only PostgreSQL and waits for its healthcheck. Loading, reducing, scoring, candidate sync, and outbox export do not use an LLM. The follow-up CLI runs on the host through uv.

### 3. Create a short CLI command

From the repository root, define this alias in Bash or WSL:

```bash
alias fue='uv run --env-file .env follow-up-engine'
```

The alias lasts for the current shell. Add the same line to your shell profile if you want it available in future sessions.

For PowerShell, use a function instead:

```powershell
function fue { uv run --env-file .env follow-up-engine @args }
```

Without either shortcut, replace `fue` in the examples below with `uv run --env-file .env follow-up-engine`.

### 4. Load the seed data

```bash
fue load
```

This creates or updates the schema and loads `seed/quotes.json` and `seed/events.jsonl`. Loading the same source again is safe.

For the included seed, the loader reports 30 quote records and 88 event input records. PostgreSQL contains 82 event rows because six repeated event IDs have exact duplicate payloads and are loaded idempotently.

### 5. Preview and persist candidates

```bash
fue sync --dry-run --now 2026-08-20T12:00:00Z
fue sync --now 2026-08-20T12:00:00Z
```

Pinning `--now` allows us to reproduce the results that we achieved on a given date compared to the seed data. On a fresh database, the dry run selects ten candidates and reports ten as new without writing a sync run or candidate rows. It may still ensure that the current schema exists. The real sync persists those ten candidates. Running it again over unchanged data does not create duplicates. Omit `--now` in normal operation to score against the current time.

Both commands accept `--limit`, `--now`, and an alternate `--policy` path.

### 6. Optional: Review live drafts

Review is the only quickstart step that requires the LLM. Add a real `ANTHROPIC_API_KEY` to `.env`, start LiteLLM and its optional usage database, and then begin review:

```bash
docker compose --profile litellm up -d --wait litellm
fue review --now 2026-08-20T12:00:00Z
```

The review flow displays pending candidates in score order and drafts messages lazily through. For each message, choose:

- `[A]pprove` — recheck quote status and the customer-wide cooldown, then create one pending outbox row
- `[R]eject` — reject the draft
- `[E]dit` — replace the message and rerun deterministic validation
- `[S]kip` — leave the draft pending for a later review; pressing Enter selects this safe default

Approval is idempotent. Re-approving the same draft cannot create another outbox row, and a contact or outbox reservation that consumes the cooldown before approval blocks the send. The primary quote's latest non-null event channel selects email or SMS; a missing channel defaults to SMS.

### 7. Export the mock outbox

```bash
fue outbox --path outbox.csv
```

The export contains every outbox row in stable ID order. Rows remain pending because this MVP has no delivery method. The source data also has no customer email-address field, so an email-channel row still carries only the customer's phone identifier and is not yet deliverable as email.

### 8. Stop the services

```bash
docker compose --profile postgres --profile litellm down
```

Named database volumes are retained. Add `--volumes` only when you intentionally want to remove the local database state.

## Run the tests

```bash
uv run pytest -q --capture=no
```

The test suite includes unit tests and PostgreSQL/Compose integration tests. Integration tests create an isolated Compose project with temporary ports and remove it after the run. Model calls are stubbed; tests do not spend Anthropic tokens.

*Note, AI was used in the formatting of the quickstart section of this document, content is largely my own*

## Follow-up policy

The policy can be found and edited in [`src/follow_up_engine/config/policy.yaml`](src/follow_up_engine/config/policy.yaml).

```text
score = reason base + amount factor + recency factor
```

The default values are functional heuristics. They were deliberately kept in policy configuration so that reason weights, thresholds, recency horizons, priorities, drafting tones, cooldowns, and other assumptions can evolve independently from candidate selection, human review, and outbox delivery. The MVP focuses on the components of a useful policy and the boundaries between them rather than attempting to prove that the coeffecients are optimal. 

The primary reason also determines the drafting tone, but the model receives only that tone, not the reason key, quote amount, viewing history, or other behavioral telemetry. With sufficient exposure and outcome data, the current base, amount, and recency factors could be estimated through regression and exported into a versioned policy, or the deterministic scorer could be replaced by a more complex ranking model.

Existing reasons can be tuned or disabled through YAML. Adding an entirely new signal currently requires a small scorer change to register the reason and define a trigger; after the scorer emits it, the candidate, tone-selection, drafting, review, and outbox pipeline remains extensible.

A quote may match multiple signals, but only its highest-scoring reason becomes the primary reason. Candidates are then grouped by customer phone number, so a customer with several open quotes appears once. Sync persists the ten highest-scoring customers by default.

| Reason | Current trigger | Why it matters |
| --- | --- | --- |
| `replied_no_answer` | The customer replied after the latest outbound contact and has not received a newer answer. | An unanswered customer should return to the front of the queue. |
| `repeat_views` | The quote was viewed on at least two distinct local calendar dates since the latest outbound contact. | Repeated interest is useful intent without exposing tracking details in the message. |
| `viewed_no_reply` | A customer viewed after the latest outbound contact, has not replied since that view, and at least 24 hours have passed. | A short delay avoids reacting too quickly while preserving a warm opportunity. |
| `high_value_quiet` | The amount is at or above the configured 75th-percentile cutoff and the quote has been quiet for at least three local calendar days. | Relative value prioritizes important work without hard-coding one currency threshold. |
| `aging` | The quote is at least 14 local calendar days old. | Older open work receives a gentle final check-in before it becomes stale. |

The default policy uses a 72-hour customer-wide cooldown and stops considering a quote at 45 local calendar days, which is also fully configurable. Calendar-day decisions use the configurable `business_timezone`, which defaults to `UTC`. The latest outbound contact is the newer of the pre-stream `quotes.last_contact_at` value and any outbound `message_sent` event. Cooldown is evaluated across every quote associated with the same customer phone value.

Accepted, dismissed, unknown-status, dead, recently contacted, or phone-less quotes do not become candidates. Status and cooldown are checked again under a customer-level database lock at approval time. An existing recent outbox reservation also consumes the cooldown, preventing concurrent reviewers from approving two messages for the same customer.

Note: in my testing I notied that `high_value_quiet` were consistenly ranked the highest, which suggests either A) the weight of cost needs to be tuned B) the percentile which is considered a "high value" quote needs to be tuned or C) we need to decrease the default weight of `high_value_quiet`

## Drafting and safety

The drafting model receives only the customer's name, optional technician name, configured tone, number of open quotes, character limit, and sign-off. It does not receive quote amounts, view counts, view timestamps, or the scoring explanation.

Generated or edited copy must be non-empty, fit within the configured character limit (320 is the current default), contain no placeholders or common configured currency symbols, codes, and terms, and remain associated only with open quotes. Generated drafts have a configurable sign-off field, which defaults to `service_team`. Requiring the technician's name is configurable and disabled by default. Every message requires human approval before it reaches the mock outbox.

## What changes for a parent company with 50 shops?

One of the major reasons that I prioritized infrastructure and extensibility for this MVP, with regards to the configurable policy, room for adapters to other data sources, and a dynamic schema, was that in a case where we have to scale this, we don't want to have anything hard-coded to a single use-case. Adding a `shop_id` to our data model would allow a single drafting engine to process and route drafts to multiple shops. It would also make sense to scope customer identity and cooldown on a shop-by-shop basis, as they may have multiple concurrent threads with different shops. A shared, company wide contact policy could be added to our config to enforce any overarching constraints. The drafting agent would need some improvements with regards to the system prompt, instead of a single hardcoded prompt, creating a table mapping `shop_id` to a given drafting prompt with corresponding tone/brand copy. We'd also need to handle the outbox routing properly, each shop will need its own outbox, with corresponding sender identities.

At a high level, moving the config out of a YAML file and into something more robust, ie a proper db table containing fields corresponding to our existing config, and allowing the manager for each shop to edit the scoring priority, change tones/reasons, edit system prompts, etc. as needed for their specific business. The review interface also needs a degree of RBAC, such that only actors associated with a given shop and approved to review drafts are able to actually use/see it. Another thing that is massively important at 50, and is much easier than with a small dataset, is evaluation. Being able to track what ranking parameters lead to the most closures, what tones/emails get the most responses, and test/rollout different policies across different shops would provide us the ability to build a robust scoring model. 

## What I would build next with another day

Each one of these could be its own entire day, so instead of choosing one I ranked my top 3 priorities for future work

1. **Improve the user experience.** Replace environment and Compose rigamarole with a first-class setup/diagnostic command, then provide either a focused web review interface or a more polished CLI with clearer navigation, search, filtering, and history. This also could be integrated into a product like... PitchPro when I come to New York next week.
3. **Add model evaluation and observability.** Turn the current LiteLLM usage database into a repeatable evaluation workflow for tone, policy alignment, safety failures, latency, cost, and prompt/model comparisons. This was intentionally left untouched for the MVP, but optimizing the quality of the drafting agent, and extending that agent's capability is an entire rabbit hole that we should probably go down.
4. **Productionize orchestration and controls.** Schedule ingestion and sync runs, add consent and opt-out enforcement, support concurrent workers, strengthen ingestion checks, and expose operational health metrics.


 One Final Caveat: During the window I set aside to complete this, my machine crashed and I had to restart twice, costing me about 20 mins of wall-clock and another 10 making sure everything was still intact. I gave myself an extra 30 mins of time to ensure that I could deliver the MVP I wanted. You can see me fighting my agent on WSL quirks a bit in the attached transcript, if I wasn't planning to export this, I probably would have used some more colorful language LOL. End to end this took me ~3:30 mins, not including making this README.