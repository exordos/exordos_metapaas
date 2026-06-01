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
"""Generic control-plane agent services for the metapaas gservice.

These are PaaS-agnostic: the infrastructure scheduler and the universal agent
that talks to Core to materialize NodeSet/Config resources. PaaS plugins
contribute their own Infra/PaaS builders, instantiated dynamically from the
plugin registry at startup — no launchpad config wiring needed.
"""
from __future__ import annotations

import logging
import os
import uuid as sys_uuid

from gcl_sdk.agents.universal import utils as ua_utils
from gcl_sdk.agents.universal.clients.orch import db as orch_db
from gcl_sdk.agents.universal.drivers import core as core_drivers
from gcl_sdk.agents.universal.services import agent as agent_service
from gcl_sdk.agents.universal.services import scheduler as scheduler_service

from exordos_metapaas.common import constants as cc

LOG = logging.getLogger(__name__)


class InfraScheduler(scheduler_service.UniversalAgentSchedulerService):
    def __init__(self, capabilities=("node_set", "config"), **kwargs):
        super().__init__(list(capabilities), **kwargs)


class UAgent(agent_service.UniversalAgentService):
    def __init__(
        self,
        core_username: str,
        core_password: str,
        core_api_base_url: str,
        project_id: str | sys_uuid.UUID,
        **kwargs,
    ):
        agent_uuid = ua_utils.system_uuid()
        orch_client = orch_db.DatabaseOrchClient()
        core_driver = core_drivers.RestCoreCapabilityDriver(
            username=core_username,
            password=core_password,
            user_api_base_url=core_api_base_url,
            project_id=project_id,
            use_project_scope=True,
            node_set="/v1/compute/sets/",
            config="/v1/config/configs/",
        )
        payload_path = os.path.join(cc.WORK_DIR, "infra_agent_payload.json")
        super().__init__(
            agent_uuid=agent_uuid,
            orch_client=orch_client,
            caps_drivers=[core_driver],
            facts_drivers=[],
            payload_path=payload_path,
            **kwargs,
        )
