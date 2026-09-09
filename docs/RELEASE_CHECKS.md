# V1 release checks and existing-database migration

Updated September 9, 2026. The release target is a local Respawned with
human review by default and explicit operator-selected automatic authorization. The outbox records reservations; it does not deliver messages. This file
is the migration procedure. The current [release record](V1_RELEASE.md) separates
completed qualification from publication and from migration of an existing installation.

## Repairs and verification boundaries

- Ingestion now completes its transaction before sending HTTP success. The
  regression records actual ASGI response events for both successful commit and
  an injected commit failure. A failure returns 500, never a success count.
  [FastAPI documents the function-scoped dependency lifetime](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/#early-exit-and-scope).
- The Docker build context now includes `LICENSE`, as required by the package's
  `license-files` metadata. An offline `uv sync --frozen --no-dev` passed in a
  fresh temporary directory containing exactly the copied files, using uv
  0.12.3 and Python 3.12.3. A complete image build remains separate.
- The named `pgdata` volume uses `DB_DATA_MOUNT`, defaulting to
  `/var/lib/postgresql/data` for the PostgreSQL 16 example. With a parent-directory
  mount on PostgreSQL 16/17, the image can store the actual database in an
  anonymous child volume. PostgreSQL 18 uses a different layout where the parent
  mount is correct; preserve that layout instead of applying the 16 default. A later
  container recreation can then select a different child volume.
  [The official PostgreSQL image documents these version-specific paths](https://hub.docker.com/_/postgres).

No existing user Docker container or volume was migrated while making these
repairs. Subsequent qualification uses separate, uniquely named projects and
synthetic data; see the [release record](V1_RELEASE.md) for its result. The ASGI test
injects a commit error; it is not a live PostgreSQL connection-loss experiment.
Changing the database major version is a separate migration. Do not downgrade
an existing PostgreSQL 18 installation to 16 or change its mount as part of V1.

| Image major | `DB_DATA_MOUNT` for the default image layout |
| --- | --- |
| PostgreSQL 16/17 | `/var/lib/postgresql/data` |
| PostgreSQL 18 | `/var/lib/postgresql` |

The existing stopped local installation was inspected read-only and uses
`postgres:18-alpine` with a parent mount. Its ignored `.env` now explicitly
preserves that mount. Its container and data were not migrated. New installations
using `.env.example` select PostgreSQL 16 and its matching data-directory mount.

## Preserve an existing database before changing its mount

Do not apply `compose up` to a populated old installation until its storage is
identified and its backup has been restored successfully elsewhere. Do not run
`down --volumes`, remove volumes, or prune Docker storage during this migration.
Keep the old database and all its named and anonymous volumes until cutover and
recovery have been verified.

The commands below use Bash/WSL from the repository root. Use the existing
installation's actual Compose project name with `-p` if it was set explicitly.
They are operator-run steps, not an automatic migration script.

1. Schedule a maintenance window and pause application/connector writers. Keep
   the original database running for backup. Identify it and save the actual
   mount list, rather than assuming the volume named `pgdata` contains its data:

   ```bash
   mkdir -p backups/v1-migration
   chmod 700 backups/v1-migration
   db_container=$(docker compose --profile app ps -q db)
   test -n "$db_container"
   docker inspect --format '{{.Id}} {{.Config.Image}}' "$db_container"
   docker inspect --format '{{json .Mounts}}' "$db_container" \
     > backups/v1-migration/original-mounts.json
   docker exec "$db_container" sh -c \
     'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SHOW data_directory"'
   ```

   Inspect the volume at that data directory using `docker volume inspect` with
   its recorded name. Record row counts and several known application IDs,
   drafts, approvals, and outbox rows for comparison after restore. If the old
   container has already disappeared, preserve all candidate volumes and
   identify/recover the original database before attempting this procedure.

2. Write a PostgreSQL custom-format backup inside the container, then copy it.
   This avoids corrupting binary output through shell redirection on Windows:

   ```bash
   docker exec "$db_container" sh -c \
     'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --file=/tmp/respawned-v1.dump'
   docker exec "$db_container" pg_restore --list /tmp/respawned-v1.dump
   docker cp "$db_container":/tmp/respawned-v1.dump backups/v1-migration/respawned-v1.dump
   chmod 600 backups/v1-migration/respawned-v1.dump
   sha256sum backups/v1-migration/respawned-v1.dump
   ```

   Check every command's exit status before continuing. Listing a dump is only
   a preliminary check; the restore below establishes that it is usable. The
   dump includes application data and must be protected. If the installation
   uses additional database roles or custom grants, also export globals with
   `pg_dumpall --globals-only`, protect that file because it can contain password
   hashes, and review/recreate the necessary roles before restoring.

3. Choose an unused Compose project name and unused host ports. Inspect
   `docker compose ls` and `docker volume ls` first. The example name below must
   be unused; the original project must remain separate. Use the corrected
   Compose file, the same PostgreSQL major version, and the original database
   name/user. Start only the new database so application initialization cannot
   create tables before restore:

   ```bash
   DB_PORT=55440 APP_PORT=18040 docker compose -p respawned-v1-restore \
     --profile app up -d --wait db
   restore_container=$(docker compose -p respawned-v1-restore --profile app ps -q db)
   test -n "$restore_container"
   docker inspect --format '{{json .Mounts}}' "$restore_container"
   docker cp backups/v1-migration/respawned-v1.dump "$restore_container":/tmp/respawned-v1.dump
   docker exec "$restore_container" sh -c \
     'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --exit-on-error --single-transaction --no-owner /tmp/respawned-v1.dump'
   ```

   Confirm the fresh named volume uses the mount for the selected image major
   shown above, and verify `SHOW data_directory` inside the restored container.
   Restore into a newly initialized, empty application database. `--no-owner`
   assigns ownership to the configured application database user; installations
   requiring distinct owners must instead restore those roles and ownership.
   Compare saved row counts and known records, including outbox reservations.

4. On this restored copy, build/start the API with the same project and port
   overrides. Verify the documented ingest, replay, review, and unsent outbox
   flow. Then stop/recreate this **new** project with `docker compose ... down`
   followed by `up`, without `--volumes`, and verify the same records remain.
   Ordinary `restart` alone does not test whether storage survives replacement
   of the container. Record the image version, commands, and results.

5. Keep original writers paused until cutover. If writes resumed after backup,
   take a new backup and repeat the restore into another fresh database before
   switching; otherwise those later changes would be lost. Point clients at the
   verified installation only after comparison and restore checks pass. Retain
   the old volumes and backups for recovery. Do not blindly switch back after
   accepting new writes; reconcile them first.

## Qualification and publication

The reusable verification commands and actual results are in the
[release record](V1_RELEASE.md). Qualify a clean Docker build, process liveness
and database readiness, the installed wheel and sdist, source replay, container
replacement persistence, and dump/restore before publishing the local V1.

The installed package now bundles the synthetic demo fixtures. `/healthz` reports
process liveness; `/readyz` checks the database, required schema, and server policy.
Neither endpoint proves model/provider availability or source freshness.

Automatic authorization is opt-in. Schema initialization adds its provenance
column while preserving existing outbox content; historical rows become
`legacy_unknown`. Review and export changes should be used only after applying
the current canonical schema with `init` or normal API startup.

Remote access remains outside the local V1 promise. Successful synthetic tests
do not establish a live mailbox integration, automatic scheduling, contextual
model quality, or actual provider delivery. Publication must target the exact
reviewed commit and artifacts; do not tag an older `main` revision merely because
its package metadata also says `1.0.0`.
