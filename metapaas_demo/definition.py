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

import os

from exordos_metapaas.registry import PaaSDefinition

from metapaas_demo.controlplane.api import routes


class DemoDefinition(PaaSDefinition):
    """Minimal PaaS plugin used to validate the metapaas plugin contract.

    Implements the full plugin interface (builders, agent model/filter maps,
    IAM controllers, infra/paas layers, DP driver) so it can serve as a
    working reference alongside the HOW_TO_BUILD_NEW_PAAS.md guide.
    """

    slug = "demo"
    element_name = "metapaas-demo"

    def get_type_route(self):
        return routes.DemoRoute

    def get_migrations_path(self):
        return os.path.join(os.path.dirname(__file__), "migrations")

    def get_builders(self, core_username, core_password, core_api_base_url, project_id):
        from metapaas_demo.controlplane.infra.dm.models import (
            DemoInstance as InfraDemoInstance,
        )
        from metapaas_demo.controlplane.infra.services.builder import CoreInfraBuilder
        from metapaas_demo.controlplane.paas.dm.models import (
            DemoInstance as PaaSDemoInstance,
        )
        from metapaas_demo.controlplane.paas.services.builder import DemoInstanceBuilder

        return [
            CoreInfraBuilder(
                core_username=core_username,
                core_password=core_password,
                core_api_base_url=core_api_base_url,
                project_id=project_id,
                instance_model=InfraDemoInstance,
            ),
            DemoInstanceBuilder(instance_model=PaaSDemoInstance),
        ]

    def get_agent_models(self):
        return {
            "versions": "metapaas_demo.controlplane.dm.models:DemoVersion",
            "instances": "metapaas_demo.controlplane.infra.dm.models:DemoInstance",
        }

    def get_agent_filters(self):
        return {
            "versions": "description",
            "instances": "project_id",
        }
