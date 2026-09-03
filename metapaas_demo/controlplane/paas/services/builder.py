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

import logging
import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.paas.services import builder
from restalchemy.storage import exceptions as storage_exceptions

from metapaas_demo.controlplane.paas.dm import models

LOG = logging.getLogger(__name__)
AGENT_UUID5_NAME = "metapaas-demo"


class PaaSBuilder(builder.PaaSBuilder):
    @classmethod
    def agent_uuid_by_node(cls, node_uuid: sys_uuid.UUID) -> sys_uuid.UUID:
        return sys_uuid.uuid5(node_uuid, AGENT_UUID5_NAME)

    def schedule_paas_objects(
        self,
        instance: ua_models.InstanceWithDerivativesMixin,
        paas_objects: tp.Collection[ua_models.TargetResourceKindAwareMixin],
    ) -> dict[sys_uuid.UUID, tp.Collection[ua_models.TargetResourceKindAwareMixin]]:
        scheduled = {}
        for entity in paas_objects:
            scheduled[entity.uuid] = [entity]
        return scheduled


class DemoInstanceBuilder(PaaSBuilder):
    def __init__(
        self,
        instance_model: type[models.DemoInstance] = models.DemoInstance,
    ):
        super().__init__(instance_model)

    def _build_paas_objects(
        self, instance: models.DemoInstance
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        try:
            nodeset = instance.get_actual_nodeset()
        except storage_exceptions.RecordNotFound:
            LOG.debug(
                "Nodeset for demo instance %s not ready yet, skipping", instance.uuid
            )
            return []

        nodes_by_idx = list(nodeset.nodes.keys())
        if not nodes_by_idx:
            return []

        # Demo is always single-node
        node_uuid = sys_uuid.UUID(nodes_by_idx[0])
        return [
            models.DemoInstanceNode(
                uuid=PaaSBuilder.agent_uuid_by_node(node_uuid),
                name=instance.name,
            )
        ]

    def create_paas_objects(
        self, instance: models.DemoInstance
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        return self._build_paas_objects(instance)

    def actualize_paas_objects(
        self,
        instance: models.DemoInstance,
        paas_collection: builder.PaaSCollection,
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        return self._build_paas_objects(instance)
