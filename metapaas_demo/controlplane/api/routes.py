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

from restalchemy.api import routes

from metapaas_demo.controlplane.api import controllers


class DemoInstanceRoute(routes.Route):
    __controller__ = controllers.DemoInstanceController


class DemoVersionRoute(routes.Route):
    __controller__ = controllers.DemoVersionController


class DemoRoute(routes.Route):
    """Handler for /v1/types/demo/ endpoint (mounted by metapaas)."""

    __controller__ = controllers.DemoController
    __allow_methods__: tp.ClassVar = [routes.FILTER]

    # /v1/types/demo/instances/[<uuid>]
    instances = routes.route(DemoInstanceRoute)
    # /v1/types/demo/versions/[<uuid>]
    versions = routes.route(DemoVersionRoute)
