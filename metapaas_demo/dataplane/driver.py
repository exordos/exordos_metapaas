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
from __future__ import annotations

import logging
import os
import typing as tp

from gcl_sdk.agents.universal import constants as c
from gcl_sdk.agents.universal.drivers import meta
from gcl_sdk.infra import constants as pc
from restalchemy.dm import properties
from restalchemy.dm import types as ra_types

from metapaas_demo import constants

LOG = logging.getLogger(__name__)


def _write_file_atomic(path: str, content: str) -> bool:
    """Write file; return True if content changed."""
    try:
        with open(path, "r") as f:
            existing = f.read()
        if existing == content:
            return False
    except FileNotFoundError:
        pass

    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.rename(tmp, path)
    return True


class DemoInstance(meta.MetaDataPlaneModel):
    """Data plane model for a single demo node.

    Writes a simple env file that the demo service reads on startup.
    """

    name = properties.property(
        ra_types.String(min_length=1, max_length=512),
        required=True,
    )
    status = properties.property(
        ra_types.Enum([s.value for s in pc.InstanceStatus]),
        default=pc.InstanceStatus.ACTIVE.value,
    )

    _meta_fields: tp.ClassVar = {"uuid", "name"}

    def get_meta_model_fields(self) -> set[str] | None:
        return self._meta_fields

    def _build_env(self) -> str:
        return (
            "# Demo node environment\n"
            "# Managed by Exordos demo control plane — do not edit manually\n"
            f"DEMO_NAME={self.name}\n"
        )

    def dump_to_dp(self) -> None:
        _write_file_atomic(constants.DEMO_ENV_FILE, self._build_env())

    def restore_from_dp(self) -> None:
        try:
            with open(constants.DEMO_ENV_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DEMO_NAME="):
                        self.name = line.split("=", 1)[1]
        except FileNotFoundError:
            pass

    def delete_from_dp(self) -> None:
        pass

    def update_on_dp(self) -> None:
        self.dump_to_dp()


class DemoCapabilityDriver(meta.MetaFileStorageAgentDriver):
    """Demo capability driver for the universal agent."""

    DEMO_META_PATH = os.path.join(c.WORK_DIR, "demo_meta.json")

    __model_map__: tp.ClassVar = {
        "demo_instance_node": DemoInstance,
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, meta_file=self.DEMO_META_PATH, **kwargs)
