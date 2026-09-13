#!/bin/sh
set -eu

# Use the reviewed Odoo runner when supplied by the development environment.
# Keeping this script small makes the fork usable both in CI and from a local
# checkout without embedding credentials or database configuration.
ODOO_BIN=${ODOO_BIN:-odoo}
DB_NAME=${DB_NAME:-community_iot_box_test}
if ! command -v "$ODOO_BIN" >/dev/null 2>&1; then
    printf '%s\n' "ODOO_BIN=$ODOO_BIN was not found; install/use the Odoo 17 test runner." >&2
    exit 2
fi
exec "$ODOO_BIN" --stop-after-init --test-enable --database="$DB_NAME" \
    --init=community_iot_box --log-level=test --without-demo=all
