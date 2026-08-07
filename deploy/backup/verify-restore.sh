#!/usr/bin/env bash
# Check a restored database before cutting over to it.
#   ./verify-restore.sh journal_restore [journal-20260806T030000Z.manifest.txt]
# Exits non-zero on the first failed check.
set -euo pipefail

TARGET_DB="${1:-}"
MANIFEST="${2:-}"
[ -n "$TARGET_DB" ] || { echo "usage: $0 <database> [manifest]" >&2; exit 2; }

COMPOSE_FILE="${COMPOSE_FILE:-/opt/journal/deploy/docker-compose.prod.yml}"
DB_SERVICE="${DB_SERVICE:-db}"
DB_CONTAINER="${DB_CONTAINER:-}"
DB_USER="${DB_USER:-journal}"

if [ -z "$DB_CONTAINER" ]; then
  DB_CONTAINER="$(docker compose -f "$COMPOSE_FILE" ps -q "$DB_SERVICE" || true)"
fi
[ -n "$DB_CONTAINER" ] || { echo "no db container" >&2; exit 2; }

case "$TARGET_DB" in
  journal) echo "refusing to verify against the live database" >&2; exit 2 ;;
esac

fails=0
q() { docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$TARGET_DB" -At -c "$1"; }

check() {  # check <label> <actual> <expected>
  if [ "$2" = "$3" ]; then
    printf 'ok    %-26s %s\n' "$1" "$2"
  else
    printf 'FAIL  %-26s got %s, want %s\n' "$1" "$2" "$3"
    fails=$((fails + 1))
  fi
}

nonzero() {  # nonzero <label> <actual>
  if [ -n "$2" ] && [ "$2" != "0" ]; then
    printf 'ok    %-26s %s\n' "$1" "$2"
  else
    printf 'FAIL  %-26s %s\n' "$1" "${2:-<empty>}"
    fails=$((fails + 1))
  fi
}

echo "verifying $TARGET_DB"

nonzero journalentry "$(q 'SELECT count(*) FROM journalentry;')"

# entry_embeddings is raw SQL, outside SQLModel.metadata — the easiest table to
# lose in a restore and not notice.
nonzero entry_embeddings "$(q 'SELECT count(*) FROM entry_embeddings;')"
check   vector_extension "$(q "SELECT count(*) FROM pg_extension WHERE extname='vector';")" 1
check   embedding_type   "$(q "SELECT atttypmod FROM pg_attribute
                               WHERE attrelid='entry_embeddings'::regclass AND attname='embedding';")" 384
nonzero embedding_indexes "$(q "SELECT count(*) FROM pg_index
                                WHERE indrelid='entry_embeddings'::regclass;")"

# A cosine search against a live row: proves the vectors survived as vectors.
nonzero similarity_query "$(q 'SELECT count(*) FROM (
    SELECT entry_id FROM entry_embeddings
    ORDER BY embedding <=> (SELECT embedding FROM entry_embeddings LIMIT 1)
    LIMIT 5) s;')"

nonzero alembic_version "$(q 'SELECT version_num FROM alembic_version;')"

if [ -n "$MANIFEST" ]; then
  echo "comparing against $(basename "$MANIFEST")"
  for t in journalentry entry_embeddings users user_api_keys alembic_version; do
    want="$(awk -F= -v k="$t" '$1==k {print $2}' "$MANIFEST")"
    [ -n "$want" ] || continue
    if [ "$t" = alembic_version ]; then
      check "manifest:$t" "$(q 'SELECT version_num FROM alembic_version;')" "$want"
    else
      check "manifest:$t" "$(q "SELECT count(*) FROM $t;")" "$want"
    fi
  done
fi

[ "$fails" -eq 0 ] || { echo "$fails check(s) failed — do not cut over"; exit 1; }
echo "all checks passed"
