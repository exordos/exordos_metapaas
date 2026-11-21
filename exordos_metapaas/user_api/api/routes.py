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

from restalchemy.api import routes

from exordos_metapaas.registry import discover_paas
from exordos_metapaas.user_api.api import controllers

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Plugin route cache
#
# Populated on first access and held for the life of the worker. A plugin
# installed or upgraded later is picked up by replacing the workers (the hub
# spawns fresh interpreters on reload), never by re-importing in place: a
# Python process can't swap an already imported plugin package.
#
# setattr on TypeRoute is required in addition to the dict cache:
# restalchemy's RoutesListController._get_target_route traverses the route
# tree via plain getattr(), bypassing our get_route() override.
# ---------------------------------------------------------------------------

_plugin_cache: dict | None = None  # {slug: route_class}
_loading: bool = False  # re-entrancy guard: importlib.metadata iterates sys.meta_path
# during entry_points(), which can trigger is_route() before
# _load_plugin_cache() returns; return {} instead of recursing.


def _load_plugin_cache() -> dict:
    """Discover installed PaaS plugins, set TypeRoute class attrs, return cache."""
    result = {}

    for slug, definition in discover_paas().items():
        route_class = routes.route(definition.get_type_route())
        setattr(TypeRoute, slug, route_class)
        result[slug] = route_class
        LOG.info("Loaded PaaS route /v1/types/%s/", slug)

    # Lazy import: app.py imports this module at top level, so importing it
    # here would be circular at load time — but by call time it's fully loaded.
    from restalchemy.api import resources as ra_resources
    from restalchemy.api import routes as ra_routes

    from exordos_metapaas.user_api.api.app import UserApiApp

    ra_resources.ResourceMap.set_resource_map(
        ra_routes.Route.build_resource_map(UserApiApp)
    )
    return result


def _get_plugin_cache() -> dict:
    global _plugin_cache, _loading
    if _plugin_cache is None:
        if _loading:
            return {}
        _loading = True
        try:
            _plugin_cache = _load_plugin_cache()
        finally:
            _loading = False
    return _plugin_cache


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TypeRoute(routes.Route):
    """Handler for /v1/types/ — the metapaas PaaS type registry.

    Supports full CRUD for PaaSType resources (em_metapaas_types db-back agent
    reconciles them here). PaaS plugin API sub-routes are mounted from the
    plugin cache, built on first access.
    """

    __controller__ = controllers.PaaSTypeController

    @classmethod
    def get_route(cls, name):
        cache = _get_plugin_cache()
        if name in cache:
            return cache[name]
        return super().get_route(name)

    @classmethod
    def is_route(cls, name):
        return name in _get_plugin_cache() or super().is_route(name)

    @classmethod
    def get_routes(cls):
        static = set(super().get_routes())
        return static | set(_get_plugin_cache().keys())


class ApiEndpointRoute(routes.Route):
    """Handler for /v1/ endpoint"""

    __controller__ = controllers.ApiEndpointController
    __allow_methods__: tp.ClassVar = [routes.FILTER]

    types = routes.route(TypeRoute)
