#!/usr/bin/env bash

#    Copyright 2026 Genesis Corporation.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

set -eu
set -x
set -o pipefail

source /usr/local/lib/exordos/lib_bootstrap.sh

GC_PATH="/opt/exordos_metapaas"
SERVICE_CONFIG="/etc/exordos_metapaas/exordos_metapaas.conf"
PG_VERSION="18"

while [ ! -f /etc/exordos_init.txt ]; do sleep 1; done

# Disable command tracing while handling secrets so they never land in the
# systemd journal / boot log. Re-enabled right after.
set +x
source /etc/exordos_init.txt

# IAM (for user-api token validation)
export IAM_USER_NAME="${IAM_USER_NAME:-exordos_metapaas}"
export IAM_USER_PASS="${IAM_USER_PASS:-exordos_metapaas}"
export PROJECT_ID="${PROJECT_ID}"
export GC_HS256_JWKS_ENCRYPTION_KEY="${GC_HS256_JWKS_ENCRYPTION_KEY:-}"
export AUDIENCE="${AUDIENCE:-}"

# Embedded control-plane database. Password is generated locally and persisted
# in the rendered config on the persistent data disk (never leaves the node).
export GC_PG_USER="${GC_PG_USER:-metapaas}"
export GC_PG_PASS="${GC_PG_PASS:-$(generate_secure_password)}"
export GC_PG_DB="${GC_PG_DB:-metapaas}"
set -x

# --- Persistent data disk ---------------------------------------------------
# The control-plane DB is stateful, so its data must survive root/image
# replacement. Move postgres (and metapaas state/config) onto the second disk.
PERSISTENT_DISK=$(find_persistent_disk)
prepare_persistent_disk "$PERSISTENT_DISK" "$PERSISTENT_MOUNT"

if [[ -n "$PERSISTENT_DISK" ]]; then
    # Migrate logs first, some processes may keep writing to root until reboot
    migrate_to_persistent_restart "/var/log" "${PERSISTENT_MOUNT}/var/log" "systemd-journald rsyslog"
    migrate_to_persistent_stop_start "/var/lib/postgresql" "${PERSISTENT_MOUNT}/var/lib/postgresql" "postgresql@${PG_VERSION}-main"
    mkdir -p /var/lib/exordos/exordos_metapaas
    migrate_to_persistent "/var/lib/exordos/exordos_metapaas" "${PERSISTENT_MOUNT}/var/lib/exordos/exordos_metapaas"
    migrate_to_persistent "/etc/exordos_metapaas" "${PERSISTENT_MOUNT}/etc/exordos_metapaas"

    persist_migrate_complete
fi

# --- Database user/db + service config --------------------------------------
CORE_AGENT_CONFIG="/etc/exordos_metapaas/core_agent.conf"
sudo mkdir -p /var/lib/exordos/exordos_metapaas/core_agent

source "$GC_PATH"/.venv/bin/activate

# First boot only: create the embedded control-plane DB user/db.
if [[ ! -f $SERVICE_CONFIG ]]; then
    set +x  # $GC_PG_PASS is passed as an argument — keep it out of the trace
    setup_postgresql_user_and_db "$GC_PG_USER" "$GC_PG_PASS" "$GC_PG_DB"
    set -x
fi

# Render the gservice + core-agent configs from the installed PaaS registry —
# the [launchpad] services and core-agent [models]/[filters] are generated, not
# hardcoded. Idempotent: the DB password is recovered from an existing config so
# it stays stable across reboots and plugin installs.
exordos-metapaas-render-config \
    --gservice-out "$SERVICE_CONFIG" \
    --core-agent-out "$CORE_AGENT_CONFIG"

# --- Apply migrations -------------------------------------------------------
# gcl_sdk universal-agent tables (needed by orch/status APIs)
ra-apply-migration --config-dir "/etc/exordos_metapaas/" \
    --path "$($GC_PATH/.venv/bin/python3 -c 'import gcl_sdk; print(gcl_sdk.__path__[0] + "/migrations")')"

# metapaas own tables (metapaas_paas_types, etc.)
ra-apply-migration --config-dir "/etc/exordos_metapaas/" \
    --path "$($GC_PATH/.venv/bin/python3 -c 'import exordos_metapaas; print(exordos_metapaas.__path__[0] + "/migrations")')"

# Per-plugin migrations, discovered from the registry (no hardcoded plugin list;
# the runtime installer applies these the same way for plugins added later).
for mpath in $($GC_PATH/.venv/bin/python3 -c '
from exordos_metapaas.registry import discover_paas
for d in discover_paas().values():
    p = d.get_migrations_path()
    if p:
        print(p)
'); do
    ra-apply-migration --config-dir "/etc/exordos_metapaas/" --path "$mpath"
done

# --- Seed CP node's own RSA key into local DB --------------------------------
# The private key is placed on disk during image provision (same key Core stores
# in ua_node_encryption_keys). DatabaseOrchClient (used by the gservice UAgent)
# looks up the key in the local DB to verify the node before registration.
python3 - <<'PYEOF'
import sys
from oslo_config import cfg
from restalchemy.common import config_opts as ra_config_opts
from restalchemy.storage.sql import engines
from restalchemy.common import contexts
from restalchemy.dm import filters as dm_filters
from gcl_sdk.agents.universal import constants as c, utils as ua_utils
from gcl_sdk.agents.universal.dm import models as ua_models

ra_config_opts.register_posgresql_db_opts(cfg.CONF)
cfg.CONF(['--config-dir', '/etc/exordos_metapaas/'])
engines.engine_factory.configure_postgresql_factory(cfg.CONF)

try:
    private_key = open(c.PRIVATE_KEY_PATH).read().strip()
except FileNotFoundError:
    print(f"Private key not found at {c.PRIVATE_KEY_PATH}, skipping key seed")
    sys.exit(0)

node_uuid = ua_utils.system_uuid()
with contexts.Context().session_manager() as session:
    existing = ua_models.NodeEncryptionKey.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(str(node_uuid))}
    )
    if existing is None:
        ua_models.NodeEncryptionKey(uuid=node_uuid, private_key=private_key).insert(session=session)
        print(f"Seeded encryption key for node {node_uuid}")
    else:
        print(f"Encryption key for {node_uuid} already present, skipping")
PYEOF

deactivate

# --- Enable services --------------------------------------------------------
sudo systemctl enable --now \
    exordos-metapaas-user-api \
    exordos-metapaas-status-api \
    exordos-metapaas-orch-api \
    exordos-metapaas-gservice \
    exordos-metapaas-core-agent

echo "Bootstrap completed successfully."
