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
WORK_DIR="/var/lib/exordos/exordos_metapaas"
VENV_PATH="$GC_PATH/.venv"
BOOTSTRAP_PATH="/var/lib/exordos/bootstrap/scripts"

SYSTEMD_SERVICE_DIR=/etc/systemd/system/

DEV_SDK_PATH="/opt/gcl_sdk"
SDK_DEV_MODE=$([ -d "$DEV_SDK_PATH" ] && echo "true" || echo "false")

# The demo dataplane is deliberately minimal: it just runs the universal agent
# whose DemoCapabilityDriver reconciles the meta model and writes demo.env. No
# service packages are needed — this image exists to prove the CP -> NodeSet ->
# DP-agent loop end-to-end alongside HOW_TO_BUILD_NEW_PAAS.md.
sudo apt update
sudo apt dist-upgrade -y
sudo apt install -y \
    libev-dev

# Create directories
sudo mkdir -p $GC_CFG_DIR
sudo mkdir -p $WORK_DIR

# Install agent config + first-boot bootstrap script
sudo cp "$GC_PATH/etc/exordos_metapaas/metapaas_demo_agent.conf" $GC_CFG_DIR/
sudo cp "$GC_PATH/etc/exordos_metapaas/logging.yaml" $GC_CFG_DIR/
sudo cp "$GC_PATH/exordos/images/dp_bootstrap.sh" $BOOTSTRAP_PATH/0100-metapaas-demo-dp-bootstrap.sh
sudo chmod +x $BOOTSTRAP_PATH/0100-metapaas-demo-dp-bootstrap.sh

# Install Python venv
cd "$GC_PATH"
uv sync
source "$VENV_PATH/bin/activate"

# In the dev mode the gcl_sdk package is installed from the local machine
if [[ "$SDK_DEV_MODE" == "true" ]]; then
    uv pip uninstall -y gcl_sdk
    uv pip install -e "$DEV_SDK_PATH"
fi

sudo ln -sf "$VENV_PATH/bin/exordos-universal-agent" "/usr/bin/exordos-universal-agent"

deactivate

# Install systemd service file
sudo cp "$GC_PATH/etc/systemd/exordos-metapaas-demo-agent.service" $SYSTEMD_SERVICE_DIR

# Enable DP agent (started on first boot by dp_bootstrap.sh)
sudo systemctl enable exordos-metapaas-demo-agent
