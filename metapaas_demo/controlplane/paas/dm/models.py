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

import typing as tp

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra import constants as pc
from gcl_sdk.infra.dm import models as sdk_models
from restalchemy.dm import filters as ra_filters
from restalchemy.dm import models as ra_models
from restalchemy.dm import properties
from restalchemy.dm import types as ra_types

from metapaas_demo.controlplane.dm import models


class DemoInstanceNode(
    ra_models.ModelWithUUID,
    ua_models.TargetResourceKindAwareMixin,
):
    status = properties.property(
        ra_types.Enum([s.value for s in pc.InstanceStatus]),
        default=pc.InstanceStatus.NEW.value,
    )
    name = properties.property(ra_types.String(min_length=1, max_length=64))

    @classmethod
    def get_resource_kind(cls) -> str:
        return "demo_instance_node"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        return frozenset(
            (
                "uuid",
                "name",
            )
        )


class DemoInstance(
    models.DemoInstance,
    ua_models.InstanceWithDerivativesMixin,
):
    __master_model__ = sdk_models.NodeSet
    __derivative_model_map__ = {
        "demo_instance_node": DemoInstanceNode,
    }

    @classmethod
    def get_resource_kind(cls) -> str:
        return "demo_instance"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        return frozenset(
            (
                "uuid",
                "name",
            )
        )

    def get_actual_nodeset(self):
        res = ua_models.Resource.objects.get_one(
            filters={
                "uuid": ra_filters.EQ(self.uuid),
                "kind": ra_filters.EQ("node_set"),
            }
        )
        return self.__master_model__.from_ua_resource(res)
