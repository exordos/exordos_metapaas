#!/usr/bin/env bash

# Copyright 2026 Genesis Corporation
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


GC_PATH="/opt/exordos_metapaas"
GC_CFG_DIR=/etc/exordos_metapaas
VENV_PATH="$GC_PATH/.venv"
BOOTSTRAP_PATH="/var/lib/exordos/bootstrap/scripts"

SYSTEMD_SERVICE_DIR=/etc/systemd/system/

DEV_SDK_PATH="/opt/gcl_sdk"
SDK_DEV_MODE=$([ -d "$DEV_SDK_PATH" ] && echo "true" || echo "false")

# Install packages (embedded postgres server for the control-plane node)
sudo apt update
sudo apt dist-upgrade -y
sudo apt install -y \
    libev-dev \
    j2cli \
    postgresql-18 \
    postgresql-client-18 \
    yq

# Disable the embedded postgres at build time. Its data dir is initialized on
# the root disk by the package; on first boot cp_bootstrap.sh migrates
# /var/lib/postgresql onto the persistent data disk before starting it.
sudo systemctl disable --now postgresql

echo "ubuntu:ubuntu" | sudo chpasswd
sudo rm -f /etc/ssh/sshd_config.d/60-cloudimg-settings.conf
sudo yq -yi '.system_info.default_user.lock_passwd |= false' /etc/cloud/cloud.cfg

# Install exordos metapaas configuration. The gservice + core-agent configs are
# generated at boot by exordos-metapaas-render-config from the PaaS registry
# (see cp_bootstrap.sh); only static assets are baked in here.
sudo mkdir -p $GC_CFG_DIR
sudo cp "$GC_PATH/etc/exordos_metapaas/logging.yaml" $GC_CFG_DIR/
sudo cp "$GC_PATH/exordos/images/cp_bootstrap.sh" $BOOTSTRAP_PATH/0100-metapaas-bootstrap.sh
sudo chmod +x $BOOTSTRAP_PATH/0100-metapaas-bootstrap.sh

cd "$GC_PATH"
uv sync
source "$GC_PATH"/.venv/bin/activate

# In the dev mode the gcl_sdk package is installed from the local machine
if [[ "$SDK_DEV_MODE" == "true" ]]; then
    uv pip uninstall -y gcl_sdk
    uv pip install -e "$DEV_SDK_PATH"
fi
deactivate

# Create links to venv
sudo ln -sf "$VENV_PATH/bin/exordos-metapaas-user-api" "/usr/bin/exordos-metapaas-user-api"
sudo ln -sf "$VENV_PATH/bin/exordos-metapaas-status-api" "/usr/bin/exordos-metapaas-status-api"
sudo ln -sf "$VENV_PATH/bin/exordos-metapaas-orch-api" "/usr/bin/exordos-metapaas-orch-api"
sudo ln -sf "$VENV_PATH/bin/exordos-metapaas-gservice" "/usr/bin/exordos-metapaas-gservice"
sudo ln -sf "$VENV_PATH/bin/exordos-metapaas-render-config" "/usr/bin/exordos-metapaas-render-config"
sudo ln -sf "$VENV_PATH/bin/exordos-metapaas-install-paas" "/usr/bin/exordos-metapaas-install-paas"
sudo ln -sf "$VENV_PATH/bin/exordos-universal-agent-db-back" "/usr/bin/exordos-universal-agent-db-back"
sudo ln -sf "$VENV_PATH/bin/ra-apply-migration" "/usr/bin/ra-apply-migration"

# Install Systemd service files
sudo cp "$GC_PATH/etc/systemd/exordos-metapaas-user-api.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-metapaas-status-api.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-metapaas-orch-api.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-metapaas-gservice.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-metapaas-core-agent.service" $SYSTEMD_SERVICE_DIR
