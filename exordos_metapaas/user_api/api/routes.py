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

import importlib
import logging
import signal

from restalchemy.api import routes

from exordos_metapaas.registry import discover_paas
from exordos_metapaas.user_api.api import controllers

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Plugin route cache
#
# Populated on first request and held forever. Cleared to None by
# invalidate_plugin_cache() — called from a SIGUSR1 handler, which
# install-paas sends after a pip install so the running workers reload
# routes without a full process restart.
#
# setattr/delattr on TypeRoute is required in addition to the dict cache:
# restalchemy's RoutesListController._get_target_route traverses the route
# tree via plain getattr(), bypassing our get_route() override. We keep both
# in sync: load → setattr; invalidate+reload → delattr stale, setattr fresh.
# ---------------------------------------------------------------------------

_plugin_cache: dict | None = None  # {slug: route_class}
_live_slugs: frozenset = frozenset()  # slugs currently setattr'd on TypeRoute
_loading: bool = False  # re-entrancy guard: importlib.metadata iterates sys.meta_path
# during entry_points(), which can trigger is_route() before
# _load_plugin_cache() returns; return {} instead of recursing.


def _load_plugin_cache() -> dict:
    """Re-discover installed PaaS plugins, update TypeRoute class attrs, return cache.

    importlib.invalidate_caches() is called first so a package installed by
    another process (install-paas) is visible without a process restart.
    """
    global _live_slugs
    importlib.invalidate_caches()
    result = {}
    new_slugs: set[str] = set()

    for slug, definition in discover_paas().items():
        route_class = routes.route(definition.get_type_route())
        setattr(TypeRoute, slug, route_class)
        new_slugs.add(slug)
        result[slug] = route_class
        LOG.info("Loaded PaaS route /v1/types/%s/", slug)

    # Remove class attrs for plugins that are no longer installed.
    for stale in _live_slugs - new_slugs:
        try:
            delattr(TypeRoute, stale)
        except AttributeError:
            pass

    _live_slugs = frozenset(new_slugs)
    # Lazy import: app.py imports this module at top level, so importing it
    # here would be circular at load time — but by call time it's fully loaded.
    from exordos_metapaas.user_api.api.app import UserApiApp
    from restalchemy.api import resources as ra_resources
    from restalchemy.api import routes as ra_routes

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


def invalidate_plugin_cache() -> None:
    """Drop the cached plugin map; next request rebuilds it from entry points."""
    global _plugin_cache
    _plugin_cache = None
    LOG.info("PaaS plugin cache invalidated; routes reload on next request")


def _sigusr1_handler(signum, frame):
    invalidate_plugin_cache()


signal.signal(signal.SIGUSR1, _sigusr1_handler)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TypeRoute(routes.Route):
    """Handler for /v1/types/ — the metapaas PaaS type registry.

    Supports full CRUD for PaaSType resources (em_metapaas_types db-back agent
    reconciles them here). PaaS plugin API sub-routes are served dynamically:
    the plugin cache is built on first access and replaced after SIGUSR1
    (sent by install-paas after a pip install), so no process restart is
    needed when a new PaaS plugin is added.
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
    __allow_methods__ = [routes.FILTER]

    types = routes.route(TypeRoute)
