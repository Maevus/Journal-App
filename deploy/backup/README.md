# Backups

Daily `pg_dump` of the `journal` database, gzipped and encrypted on the box
before it is written anywhere, then copied to a configurable offsite
destination. Old local runs are pruned.

**The box holds only a public key.** Backups are encrypted with `age` to a
recipient whose private half never lands on the server, so a stolen VPS image
yields no readable journal and no BYO API keys. That property is the reason for
`age` here rather than `gpg` or `openssl enc` — one flag, no keyring, no
symmetric passphrase sitting in `/etc` next to the ciphertext it unlocks.

The corollary: **the server cannot verify its own backups.** Restore rehearsals
happen wherever the private key lives. Do one now and one after any schema
change.

## Files

| | |
|---|---|
| `backup.sh` | Dump, compress, encrypt, push offsite, prune. Config block at the top, all env-overridable. |
| `verify-restore.sh` | Checks a restored database and compares it to the run's manifest. |
| `journal-backup.service` | Oneshot unit. |
| `journal-backup.timer` | Daily schedule — change `OnCalendar=` here, not in the script. |
| `backup.env.example` | Copy to `/etc/journal-backup/backup.env`. |

Each run writes three files, named by UTC timestamp:

```
journal-20260806T031700Z.sql.gz.age        the database
globals-20260806T031700Z.sql.gz.age        roles (cluster-level, not in the db dump)
journal-20260806T031700Z.manifest.txt      row counts + sha256, plaintext
```

The manifest is the numbers you restore against. It holds counts only, no entry
content.

## Install

Generate the key pair **on your laptop, not the server**:

```sh
brew install age            # macOS;  apt install age  on Debian/Ubuntu
age-keygen -o journal-backup.key
```

`age-keygen` prints `Public key: age1…`. That public line goes on the server.
The file `journal-backup.key` is the only thing that can ever read a backup —
put it in your password manager, and a copy somewhere that does not depend on
this laptop still existing. Losing it makes every backup permanently unreadable.

On the server:

```sh
apt install age
install -d -m 700 /etc/journal-backup
cp /opt/journal/deploy/backup/backup.env.example /etc/journal-backup/backup.env
chmod 600 /etc/journal-backup/backup.env
$EDITOR /etc/journal-backup/backup.env      # AGE_RECIPIENT=age1… and OFFSITE_TARGET

cp /opt/journal/deploy/backup/journal-backup.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now journal-backup.timer
```

Paths in the unit assume the repo is at `/opt/journal`. Adjust `ExecStart=` if
it is not.

Run one immediately and watch it:

```sh
systemctl start journal-backup.service
journalctl -u journal-backup.service -n 50 --no-pager
systemctl list-timers journal-backup.timer
```

## Offsite

`OFFSITE_TARGET` defaults to `/var/backups/journal/offsite` — the same disk.
**That is not a backup**; it exercises the code path so the seam is known to
work before you pick a destination. Change it to one of:

```sh
OFFSITE_TARGET=/mnt/other-volume/journal            # another volume
OFFSITE_TARGET=ssh:backup@otherhost:/srv/journal    # scp, needs a key for root
OFFSITE_TARGET=rclone:b2:journal-backups/prod       # object storage
```

`rclone` is the easiest route to object storage (B2, R2, S3, Storj):

```sh
apt install rclone
rclone config              # creates the remote, e.g. named b2
rclone lsd b2:             # confirm it works as root, since the timer runs as root
```

`rclone config` writes to `~/.config/rclone/rclone.conf` for the user running
it — configure it as `root`, or set `RCLONE_CONFIG` in `backup.env`.

Give the bucket credentials **write-only/append access if the provider supports
it**, and turn on object lock or a lifecycle rule. Otherwise anything that
compromises the box can also delete the offsite copies.

Local pruning keeps the newest `KEEP` runs (default 14). Offsite retention is
the destination's job — a bucket lifecycle rule, or a separate `rclone delete
--min-age`. `backup.sh` never deletes anything offsite.

The seam is the `push_offsite()` function in `backup.sh`. It receives one file
path per artifact; replace its body for anything the three cases above miss.

---

# Restoring

Work top to bottom. Nothing here touches the live database until step 5, and
step 5 renames rather than drops.

Set the compose path once:

```sh
cd /opt/journal/deploy
DC="docker compose -f docker-compose.prod.yml"
```

### 1. Pick the backup and get it to the key

The private key should not travel to the server. Bring the file to the machine
that has the key instead.

```sh
ssh journal-vps 'ls -lt /var/backups/journal/'
scp journal-vps:/var/backups/journal/journal-20260806T031700Z.sql.gz.age .
scp journal-vps:/var/backups/journal/journal-20260806T031700Z.manifest.txt .
```

Confirm the file is intact before trusting it:

```sh
sha256sum journal-20260806T031700Z.sql.gz.age   # compare to sha256= in the manifest
```

### 2. Check it decrypts, before you need it to

```sh
age -d -i journal-backup.key journal-20260806T031700Z.sql.gz.age | gunzip | head -40
```

You should see `-- PostgreSQL database dump` and `CREATE EXTENSION … vector`.
No output, or an `age: error`, means wrong key or a truncated file — stop and
try the previous run.

Confirm the pgvector table is actually in there. It is a raw-SQL table outside
`SQLModel.metadata`, so it is the one thing worth checking by eye:

```sh
age -d -i journal-backup.key journal-20260806T031700Z.sql.gz.age | gunzip |
  grep -c 'entry_embeddings'
```

Zero here means the backup is not usable for search, whatever else it contains.

### 3. Restore into a scratch database

Never into `journal`. The dump contains no `DROP` statements and expects an
empty database.

```sh
$DC exec -T db psql -U journal -d postgres -c 'CREATE DATABASE journal_restore OWNER journal;'
```

Pipe from where the key is, into the server:

```sh
age -d -i journal-backup.key journal-20260806T031700Z.sql.gz.age | gunzip |
  ssh journal-vps 'docker exec -i $(docker compose -f /opt/journal/deploy/docker-compose.prod.yml ps -q db) \
    psql -U journal -d journal_restore -v ON_ERROR_STOP=1'
```

`ON_ERROR_STOP=1` is load-bearing. Without it `psql` prints errors, keeps going,
and exits 0 on a half-restored database.

Use the container's `psql`, not one on your laptop — a plain dump from Postgres
18 contains `\restrict` meta-commands that older `psql` builds do not parse.

If roles are missing (`role "…" does not exist`), restore the globals first:

```sh
age -d -i journal-backup.key globals-20260806T031700Z.sql.gz.age | gunzip |
  ssh journal-vps 'docker exec -i … psql -U journal -d postgres'
```

### 4. Verify

```sh
ssh journal-vps
cd /opt/journal/deploy/backup
./verify-restore.sh journal_restore /var/backups/journal/journal-20260806T031700Z.manifest.txt
```

It refuses to run against `journal`, and checks:

- `journalentry` and `entry_embeddings` are both non-empty
- the `vector` extension is installed
- `embedding` is still `vector(384)`, not text or bytea
- `entry_embeddings` has its indexes, including the HNSW one
- a real cosine query (`<=>`) returns rows — proof the vectors survived as vectors
- `alembic_version` matches
- every count matches the manifest

Expected output:

```
ok    journalentry               1092
ok    entry_embeddings           1092
ok    vector_extension           1
ok    embedding_type             384
ok    embedding_indexes          3
ok    similarity_query           5
ok    alembic_version            578f7a3dcc02
all checks passed
```

If `entry_embeddings` is empty but `journalentry` is fine, the journal is safe
and only search is broken — rebuild rather than roll back:

```sh
$DC run --rm api python scripts/embed_backfill.py
```

Then log in against the scratch database and read a few entries. Counts can be
right while the content is wrong.

### 5. Cut over

Only after step 4 passes. This renames the live database, so it is reversible
until you drop the old one.

```sh
$DC stop api
```

```sh
$DC exec -T db psql -U journal -d postgres <<'SQL'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'journal';
ALTER DATABASE journal RENAME TO journal_broken;
ALTER DATABASE journal_restore RENAME TO journal;
SQL
```

```sh
$DC start api
$DC logs -f api
```

Check the app: sign in, read a recent entry, run a search (that exercises
pgvector), watch a synthesis stream.

### 6. Afterwards

Keep `journal_broken` until you are sure — it is the only copy of anything
written after the backup ran.

```sh
$DC exec -T db psql -U journal -d postgres -c 'DROP DATABASE journal_broken;'
```

Then take a fresh backup, because the timer's next run is now the only one that
reflects the restored state:

```sh
systemctl start journal-backup.service
```

## Restore test log

| Date | Result |
|---|---|
| 2026-08-06 | Passed against local `journal-postgres` (PG 18.4, pgvector 0.8.5). Restored into scratch `journal_restore_test`: 1092 `journalentry`, 1092 `entry_embeddings`, `vector(384)` intact, HNSW index present, cosine query returned rows, `alembic_version` 578f7a3dcc02. 2.5 MB encrypted. |

Add a row each time. A backup nobody has restored is a hypothesis.

## Notes

- `pg_dump` runs **inside** the db container, so its version always matches the
  server. That is why the script uses `docker exec` rather than a host
  `pg_dump`, which drifts on the next `apt upgrade`.
- No password is needed: the container's `pg_hba.conf` trusts the local unix
  socket, and the dump never crosses the network in plaintext.
- The dump is a consistent snapshot in one transaction. It does not block
  writes, and the app can stay up.
- `flock` keeps a manual run and the timer from overlapping.
- Artifacts are written to `.part` and renamed only when the whole pipe
  succeeded, so a truncated file is never mistaken for a backup.
- Everything is `umask 077`. The manifest is plaintext and readable to root
  only; the `.age` files are safe at rest regardless.
- The globals dump contains the `journal` role's SCRAM password verifier, and
  the database dump contains the encrypted BYO API keys. Neither is safe
  unencrypted anywhere, including in transit to the offsite destination.
