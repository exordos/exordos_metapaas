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

from gcl_iam import controllers as iam_controllers
from restalchemy.api import controllers as ra_controllers
from restalchemy.api import resources as ra_resources

from exordos_metapaas.dm import models as dm_models
from exordos_metapaas.user_api.api import versions


class ApiEndpointController(ra_controllers.RoutesListController):
    """Controller for /v1/ endpoint"""

    __TARGET_PATH__ = f"/{versions.API_VERSION_1_0}/"


class PaaSTypeController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """Controller for /v1/types/ — the metapaas PaaS type registry.

    A PaaS provider manifest imports this collection and creates a resource
    here, declaring the Python package and element name. The db-back core-agent
    (em_metapaas_types) reconciles the manifest resource into this table; the
    PluginReconciler then pip-installs the package at runtime.
    """

    __policy_service_name__ = "exordos_metapaas"
    __policy_name__ = "paas_type"

    __resource__ = ra_resources.ResourceByRAModel(
        model_class=dm_models.PaaSType,
        convert_underscore=False,
        process_filters=True,
    )
