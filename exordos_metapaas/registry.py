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
"""PaaS plugin contract and discovery.

A PaaS plugin is an installable Python distribution that registers a
``PaaSDefinition`` via the ``exordos_metapaas_paas`` entry-point group. The
metapaas runtime discovers all installed definitions at process start and
mounts each one's declarative API under ``/v1/types/<slug>/``.

This mirrors the proven gcl_sdk universal-agent driver mechanism
(``gcl_sdk_universal_agent`` entry points loaded by name from config).
"""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import logging
import typing as tp

from exordos_metapaas.common import constants as c

LOG = logging.getLogger(__name__)


class PaaSDefinition:
    """Contract a PaaS plugin implements.

    Only the control-plane facing surface needed by the metapaas runtime is
    declared here. The dataplane driver ships separately inside the dataplane
    image (via the gcl_sdk universal-agent entry points).

    Everything the metapaas runtime needs to host a PaaS — its API route, its
    migrations, the control-plane builder services, the core-agent model/filter
    map, and the IAM permissions it requires — is declared by the plugin and
    discovered at runtime. The runtime never hardcodes per-PaaS knowledge: the
    gservice ``[launchpad]`` services, the core-agent ``[models]``/``[filters]``
    and the element's IAM permission set are all generated from this contract
    (see ``exordos_metapaas.cmd.render_config``).
    """

    #: short type identifier; used as the table prefix and the
    #: ``/v1/types/<slug>/`` mount point. Must be unique across plugins.
    slug: str = ""

    #: exordos element name under which clients reference this PaaS in manifests.
    #: e.g. ``"s3aas"`` → clients declare ``$s3aas.types.s3.instances``.
    #: The metapaas core-agent uses this to subscribe to the correct EM kinds:
    #: ``em_<element_name>_types_<slug>_<subpath>``.
    element_name: str = ""

    def get_type_route(self):
        """Return a restalchemy ``Route`` class for ``/v1/types/<slug>/``."""
        raise NotImplementedError

    def get_migrations_path(self) -> tp.Optional[str]:
        """Return the filesystem path of this plugin's migrations dir."""
        return None

    # --- registry-driven gservice / core-agent / IAM wiring ----------------

    def get_builders(
        self,
        core_username: str,
        core_password: str,
        core_api_base_url: str,
        project_id: str,
    ) -> tp.List:
        """Instantiate and return the control-plane builder services for this PaaS.

        Called by the gservice at startup (and after plugin install + restart).
        Return a list of ``BasicService`` instances — typically one
        ``CoreInfraBuilder`` and one ``PaaSBuilder`` subclass. The generic
        ``InfraScheduler``, ``UAgent``, and ``PluginReconciler`` are added by
        the runtime unconditionally; do not include them here.
        """
        return []

    def get_agent_models(self) -> tp.Dict[str, str]:
        """core-agent ``[models]`` map: ``{subpath: "module:Model"}``.

        ``subpath`` is the resource path *relative to* ``types.<slug>``, with
        nesting expressed by dots, e.g. ``"instances"`` or
        ``"instances.users.access_keys"``. The renderer builds the full
        universal-agent kind ``em_<element>_types_<slug>_<subpath>`` so it
        matches exactly the target-resource kind the element-manager emits for
        ``$<element>.types.<slug>.<subpath>`` (see Core
        ``elements.dm.models.Resource.kind``). The db-back universal agent then
        reconciles those target resources into the shared DB via the model and
        reports their actual state back to Core.
        """
        return {}

    def get_agent_filters(self) -> tp.Dict[str, str]:
        """core-agent ``[filters]`` map: ``{subpath: "field"}``.

        Same ``subpath`` keys as :meth:`get_agent_models`. ``field`` is the
        model column scoped to the metapaas project; the renderer emits
        ``field:<project_id>``. Most resources scope by ``project_id``; some
        (e.g. shared versions) scope by another column.
        """
        return {}


def discover_paas() -> dict[str, PaaSDefinition]:
    """Discover all installed PaaS definitions, keyed by slug."""
    result: dict[str, PaaSDefinition] = {}
    for ep in importlib_metadata.entry_points(group=c.EP_PAAS):
        obj = ep.load()
        definition = obj() if isinstance(obj, type) else obj
        if not getattr(definition, "slug", ""):
            LOG.warning("Skipping PaaS definition without slug: %s", ep.name)
            continue
        if definition.slug in result:
            raise ValueError(f"Duplicate PaaS slug '{definition.slug}'")
        result[definition.slug] = definition
        LOG.info("Discovered PaaS plugin: %s (%s)", definition.slug, ep.name)
    return result
