#!/usr/bin/env bash

set -Eeuo pipefail

oom_kill_count() {
    if [ -r /sys/fs/cgroup/memory.events ]; then
        awk '$1 == "oom_kill" { print $2; found = 1 } END { if (!found) print 0 }' /sys/fs/cgroup/memory.events
    else
        printf '0\n'
    fi
}

build_app_assets() {
    local app="$1"
    local attempt=1
    local maximum_attempts=2
    local before_oom_kills
    local after_oom_kills
    local status

    while [ "$attempt" -le "$maximum_attempts" ]; do
        before_oom_kills="$(oom_kill_count)"
        echo "Building assets for ${app} (attempt ${attempt}/${maximum_attempts})..."

        if bench build --app "$app"; then
            return 0
        else
            status=$?
        fi

        after_oom_kills="$(oom_kill_count)"
        if [ "$after_oom_kills" -gt "$before_oom_kills" ] && [ "$attempt" -lt "$maximum_attempts" ]; then
            echo "Asset build for ${app} was OOM-killed; retrying once..." >&2
            attempt=$((attempt + 1))
            continue
        fi

        echo "Asset build for ${app} failed (exit ${status}); initialization aborted." >&2
        if [ "$after_oom_kills" -gt "$before_oom_kills" ]; then
            echo "Docker ran out of memory. Increase Docker Desktop memory before retrying." >&2
        fi
        exit "$status"
    done
}

init_complete_file="/home/frappe/frappe-bench/sites/.helpdesk-init-complete"

if [ -f "$init_complete_file" ]; then
    echo "Bench initialization is complete, starting services"
    cd frappe-bench
    bench start
fi

if [ -d "/home/frappe/frappe-bench" ]; then
    echo "Previous initialization did not complete; refusing to start a partial bench." >&2
    echo "Recreate the frappe container and project volumes before retrying." >&2
    exit 1
fi

echo "Creating new bench..."

bench init --skip-redis-config-generation frappe-bench --version version-15

cd frappe-bench

# Use containers instead of localhost
bench set-mariadb-host mariadb
bench set-redis-cache-host redis://redis:6379
bench set-redis-queue-host redis://redis:6379
bench set-redis-socketio-host redis://redis:6379

# Remove redis, watch from Procfile
sed -i '/redis/d' ./Procfile
sed -i '/watch/d' ./Procfile

bench get-app telephony --skip-assets
bench get-app helpdesk --branch main --skip-assets
build_app_assets telephony
build_app_assets helpdesk

bench new-site helpdesk.localhost \
--force \
--mariadb-root-password 123 \
--admin-password admin \
--no-mariadb-socket

bench --site helpdesk.localhost install-app telephony
bench --site helpdesk.localhost install-app helpdesk
bench --site helpdesk.localhost set-config developer_mode 1
bench --site helpdesk.localhost set-config mute_emails 1
bench --site helpdesk.localhost set-config server_script_enabled 1
bench --site helpdesk.localhost clear-cache
bench use helpdesk.localhost
touch "$init_complete_file"

bench start
