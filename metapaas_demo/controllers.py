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

from restalchemy.api import controllers as ra_controllers
from restalchemy.api import resources as ra_resources

from metapaas_demo import models


class DemoController(ra_controllers.RoutesListController):
    """Controller for /v1/types/demo/ endpoint"""

    __TARGET_PATH__ = "/v1/types/demo/"


class DemoInstanceController(ra_controllers.BaseResourceControllerPaginated):
    """CRUD controller for /v1/types/demo/instances/"""

    __resource__ = ra_resources.ResourceByRAModel(
        model_class=models.DemoInstance,
        convert_underscore=False,
        process_filters=True,
    )
