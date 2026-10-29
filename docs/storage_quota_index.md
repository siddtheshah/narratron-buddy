# Theater storage quota index

Quota checks read `theaters.storage_bytes` from the database instead of walking
every owned theater's directories. The value is the larger of the saved package
and the working copy, preserving the previous accounting rule.

A NULL total means the theater needs to be measured. Existing deployments are
backfilled on their first quota check. Later checks scan only theaters changed
since their last measurement. Deleting a deployment removes its indexed total
with the deployment row.

Application writes invalidate the affected theater through the storage-change
callbacks on `TheaterManager`, `TheaterRepository`, `TheaterBuilderStore`, and
`DiskArtifactService`. Runtime image, music, animation, reference, chat, and canvas
writes use the theater's callback too. New write paths must notify this callback,
including when a failed operation leaves partially written files.

Each invalidation increments `storage_revision`. A scan only stores its result
if the revision still matches, so a concurrent write cannot be hidden by an
older scan completing afterward.

## Deployment

Before starting the updated application against an existing Cloud SQL database,
apply the additive migration as the database owner:

```powershell
uv run python scripts/migrate_cloud_sql_schema.py
```

Local databases add the columns automatically when opened. No filesystem
backfill is required before startup; the first quota check measures legacy rows.

## Maintenance

Changes made directly to storage outside the application need explicit
invalidation. To remeasure a theater after such a change, run this SQL with its
actual ID (or call `db.invalidate_theater_storage(theater_id)`):

```sql
UPDATE theaters
SET storage_bytes = NULL, storage_revision = storage_revision + 1
WHERE theater_id = 'theater_id_to_remeasure';
```

The next quota check measures that theater. Repeating the statement without the
WHERE clause rebuilds all totals lazily. This index does not introduce atomic
quota reservations: simultaneous uploads can still independently pass a quota
check, as before.
