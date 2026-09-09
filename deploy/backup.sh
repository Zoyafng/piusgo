#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
mkdir -p deploy/backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
compose=(docker compose -p piusgo-cloud --env-file deploy/.env -f deploy/compose.yaml)
# Stop writers so database and uploaded images describe the same point in time.
running=()
for service in backend worker frontend proxy; do
  container=$("${compose[@]}" --profile mail ps -q "$service")
  if [[ -n "$container" ]] && [[ $(docker inspect -f '{{.State.Running}}' "$container") == true ]]; then
    running+=("$service")
  fi
done
restore() {
  if (( ${#running[@]} )); then "${compose[@]}" --profile mail start "${running[@]}"; fi
}
trap restore EXIT
if (( ${#running[@]} )); then "${compose[@]}" --profile mail stop "${running[@]}"; fi
"${compose[@]}" exec -T postgres pg_dump -U piusgo_owner -d piusgo -Fc > "deploy/backups/$stamp.dump.tmp"
mv "deploy/backups/$stamp.dump.tmp" "deploy/backups/$stamp.dump"
"${compose[@]}" run --rm --no-deps backend tar -C /app/backend/data -czf - product-images support-attachments > "deploy/backups/$stamp-files.tar.gz.tmp"
mv "deploy/backups/$stamp-files.tar.gz.tmp" "deploy/backups/$stamp-files.tar.gz"
printf 'Backup saved: deploy/backups/%s (database and images)\n' "$stamp"
