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

"""Build the MetaPaaS user API specification without services or plugins."""

import json
from unittest import mock

from restalchemy.api import applications
from restalchemy.api import constants as api_constants
from restalchemy.api import contexts as api_contexts
from restalchemy.common import contexts
import webob

from exordos_metapaas.user_api.api import app
from exordos_metapaas.user_api.api import routes

OPENAPI_VERSION = "3.0.3"


def build(version: str = OPENAPI_VERSION) -> dict:
    """Describe the runtime API; each PaaS plugin publishes its own routes."""
    context = contexts.ContextWithStorage()
    context.iam_context = mock.Mock()
    context.iam_context.introspection_info.return_value = {}
    context.iam_context.enforcer.enforce.return_value = True
    context.iam_context.enforcer.enforce_raw.return_value = True
    # TypeRoute's children are entirely plugin-provided. Skip discovery and
    # any previously cached plugin routes without changing the runtime cache.
    with (
        context.context_manager(),
        mock.patch.object(routes.TypeRoute, "get_routes", return_value=set()),
    ):
        application = applications.OpenApiApplication(
            route_class=app.get_api_application(),
            openapi_engine=app.get_openapi_engine(),
            warm_up_openapi=False,
        )
        request = webob.Request.blank("/")
        request.application = application
        request.api_context = api_contexts.RequestContext(request)
        request.api_context.set_active_method(api_constants.GET)
        specification = application.openapi_engine.build_openapi_specification(
            version=version, request=request
        )
    specification["info"]["version"] = "latest"
    specification["servers"][0]["url"] = "http://127.0.0.1:8080"
    return json.loads(json.dumps(specification))
