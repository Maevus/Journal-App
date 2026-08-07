#!/usr/bin/env bash
# Encrypted Postgres backup: pg_dump out of the db container, gzip, age-encrypt,
# copy offsite, prune old local runs. Safe to re-run; see ./README.md.
set -euo pipefail

# --- config ----------------------------------------------------------------
# Every value is env-overridable. Under systemd they come from
# /etc/journal-backup/backup.env (see backup.env.example).
# The schedule is not here — it is OnCalendar= in journal-backup.timer.

BACKUP_DIR="${BACKUP_DIR:-/var/backups/journal}"
KEEP="${KEEP:-14}"                                  # newest N local runs kept

# Offsite destination. Local path, rclone:remote:path, or ssh:user@host:/path.
# The default is the same disk, which is not a backup — see README.
OFFSITE_TARGET="${OFFSITE_TARGET:-$BACKUP_DIR/offsite}"

# Public keys only. The box can write backups it cannot read.
AGE_RECIPIENT="${AGE_RECIPIENT:-}"
AGE_RECIPIENTS_FILE="${AGE_RECIPIENTS_FILE:-/etc/journal-backup/recipients.txt}"

COMPOSE_FILE="${COMPOSE_FILE:-/opt/journal/deploy/docker-compose.prod.yml}"
DB_SERVICE="${DB_SERVICE:-db}"
DB_CONTAINER="${DB_CONTAINER:-}"                    # set to skip compose lookup
DB_USER="${DB_USER:-journal}"
DB_NAME="${DB_NAME:-journal}"
# ---------------------------------------------------------------------------

umask 077

log()  { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die()  { log "ERROR: $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

have docker || die "docker not found"
have age    || die "age not found — see README (apt install age)"

mkdir -p "$BACKUP_DIR"

# One run at a time; a manual run and the timer can otherwise overlap.
if have flock && [ -z "${JOURNAL_BACKUP_LOCKED:-}" ]; then
  exec env JOURNAL_BACKUP_LOCKED=1 flock -n "$BACKUP_DIR/.lock" "$0" "$@"
fi

if [ -z "$DB_CONTAINER" ]; then
  [ -f "$COMPOSE_FILE" ] || die "no COMPOSE_FILE at $COMPOSE_FILE and no DB_CONTAINER set"
  DB_CONTAINER="$(docker compose -f "$COMPOSE_FILE" ps -q "$DB_SERVICE" || true)"
fi
[ -n "$DB_CONTAINER" ] || die "no running container for service '$DB_SERVICE'"

age_args=()
if [ -n "$AGE_RECIPIENT" ]; then
  age_args=(-r "$AGE_RECIPIENT")
elif [ -r "$AGE_RECIPIENTS_FILE" ]; then
  age_args=(-R "$AGE_RECIPIENTS_FILE")
else
  die "no recipient: set AGE_RECIPIENT or create $AGE_RECIPIENTS_FILE"
fi

sha256_of() {
  if have sha256sum; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# The seam. Point OFFSITE_TARGET elsewhere, or replace this body outright.
push_offsite() {  # push_offsite <file>
  [ -n "$OFFSITE_TARGET" ] || return 0
  case "$OFFSITE_TARGET" in
    rclone:*)
      have rclone || die "OFFSITE_TARGET wants rclone and it is not installed"
      rclone copyto -- "$1" "${OFFSITE_TARGET#rclone:}/$(basename "$1")" ;;
    ssh:*)
      scp -q -- "$1" "${OFFSITE_TARGET#ssh:}/" ;;
    *)
      mkdir -p "$OFFSITE_TARGET" && cp -- "$1" "$OFFSITE_TARGET/" ;;
  esac
}

# Encrypted stream to a .part file, renamed only once the whole pipe succeeded.
dump_encrypted() {  # dump_encrypted <outfile> <pg command...>
  local out="$1"; shift
  docker exec "$DB_CONTAINER" "$@" | gzip -9 | age "${age_args[@]}" > "$out.part"
  mv "$out.part" "$out"
  log "wrote $(basename "$out") ($(wc -c <"$out" | tr -d ' ') bytes)"
  push_offsite "$out"
}

# Newest KEEP kept; timestamped names sort chronologically.
prune() {  # prune <name-glob>
  find "$BACKUP_DIR" -maxdepth 1 -type f -name "$1" | sort -r | awk -v k="$KEEP" 'NR>k' |
    while IFS= read -r f; do rm -f "$f"; log "pruned $(basename "$f")"; done
}

ts="$(date -u +%Y%m%dT%H%M%SZ)"
db_out="$BACKUP_DIR/journal-$ts.sql.gz.age"
globals_out="$BACKUP_DIR/globals-$ts.sql.gz.age"
manifest="$BACKUP_DIR/journal-$ts.manifest.txt"

trap 'rm -f "$db_out.part" "$globals_out.part" "$manifest.part"' EXIT

log "backing up $DB_NAME from container $DB_CONTAINER"

# Row counts to restore against. Counts only, no entry content, so plaintext.
# Advisory: a schema change must not stop the dump.
{
  printf 'timestamp=%s\n' "$ts"
  docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -At -F= \
    -c "SELECT 'journalentry', count(*)::text FROM journalentry
        UNION ALL SELECT 'entry_embeddings', count(*)::text FROM entry_embeddings
        UNION ALL SELECT 'users', count(*)::text FROM users
        UNION ALL SELECT 'user_api_keys', count(*)::text FROM user_api_keys
        UNION ALL SELECT 'alembic_version', (SELECT version_num FROM alembic_version LIMIT 1)
        UNION ALL SELECT 'pgvector', (SELECT extversion FROM pg_extension WHERE extname='vector')
        UNION ALL SELECT 'server_version', current_setting('server_version');" \
    || log "WARNING: manifest query failed"
} > "$manifest.part"

# pg_dump runs inside the container, so its version always matches the server.
# No --clean: the dump has no DROPs and must go into an empty database.
dump_encrypted "$db_out" pg_dump --username "$DB_USER" --dbname "$DB_NAME" \
  --format=plain --encoding=UTF8

# Roles are cluster-level and not in the per-database dump.
dump_encrypted "$globals_out" pg_dumpall --username "$DB_USER" --roles-only

printf 'sha256=%s\n' "$(sha256_of "$db_out")" >> "$manifest.part"
mv "$manifest.part" "$manifest"
push_offsite "$manifest"

prune 'journal-*.sql.gz.age'
prune 'globals-*.sql.gz.age'
prune 'journal-*.manifest.txt'

log "done: $db_out"
