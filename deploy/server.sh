#!/usr/bin/env bash
set -euo pipefail
umask 077
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$PROJECT_ROOT"

if [[ ! -f .env.nginx ]]; then
    echo 'Run python3 deploy/prepare.py YOUR_DOMAIN first.' >&2
    exit 1
fi
command -v docker >/dev/null || { echo 'Docker with Compose v2 is required.' >&2; exit 1; }
docker compose version >/dev/null

compose() {
    docker compose --project-directory "$PROJECT_ROOT" --env-file "$PROJECT_ROOT/.env.nginx" \
        -f "$PROJECT_ROOT/compose.nginx.yaml" "$@"
}

backup() (
    # Stop writers for a consistent database + image snapshot; always resume them.
    local running=() service target running_services
    running_services=$(compose ps --status running --services)
    while IFS= read -r service; do
        [[ "$service" == app || "$service" == web ]] && running+=("$service")
    done <<< "$running_services"
    compose exec -T db pg_isready -U schedule -d schedule >/dev/null
    target="$PROJECT_ROOT/backups/server/$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mkdir -p -- "$target"
    trap 'backup_status=$?; if ((${#running[@]})); then if ! compose start --wait --wait-timeout 240 "${running[@]}"; then backup_status=1; fi; fi; exit "$backup_status"' EXIT
    compose stop web app
    compose exec -T db pg_dump -U schedule -d schedule -Fc > "$target/database.dump"
    compose run --rm --no-deps --entrypoint tar app -C /app/media -czf - . > "$target/media.tar.gz"
    cp -- .env.nginx "$target/production.env"
    git rev-parse HEAD > "$target/revision.txt" 2>/dev/null || echo 'Source archive' > "$target/revision.txt"
    test -s "$target/database.dump"
    test -s "$target/media.tar.gz"
    printf 'Database, media and configuration saved at %s\n' "$(date -u +%FT%TZ)" > "$target/COMPLETE"
    printf 'Backup: %s\n' "$target"
)

case "${1:-help}" in
    up)
        compose up -d --build --wait --wait-timeout 240
        compose exec -T app python manage.py check --deploy --fail-level WARNING
        echo 'Ready. Connect your HTTPS Nginx site using deploy/generated/nginx.conf or location.conf.'
        ;;
    update)
        # Build first so a build failure does not interrupt the running site.
        compose build app
        backup
        compose stop web app
        compose up -d --wait --wait-timeout 240
        compose exec -T app python manage.py check --deploy --fail-level WARNING
        ;;
    backup) backup ;;
    admin) compose exec app python manage.py createsuperuser ;;
    manage) shift; compose exec -T app python manage.py "$@" ;;
    logs) compose logs --tail=100 -f ;;
    status) compose ps ;;
    stop) compose stop ;;
    *) echo 'Usage: bash deploy/server.sh {up|update|backup|admin|manage ...|logs|status|stop}' ;;
esac
