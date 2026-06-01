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
"""Runtime data models owned by the metapaas runtime itself (not a PaaS plugin).

``PaaSType`` is the declarative registration of a PaaS plugin into this
metapaas control plane. A PaaS provider manifest (e.g. s3aas.yaml.j2) imports
the ``$metapaas.types`` collection and creates a resource there, declaring the
Python package and the element name under which the PaaS exposes its API to
clients. The db-back core-agent (kind ``em_metapaas_types``) reconciles these
into the ``metapaas_paas_types`` table, and the PluginReconciler pip-installs
any package not yet present (see ``exordos_metapaas.services.plugin_reconciler``).
"""
from gcl_sdk.agents.universal.dm import models as ua_models  # patches SQLStorableMixin
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import types
from restalchemy.storage.sql import orm


class PaaSType(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithProject,
    models.ModelWithTimestamp,
    ua_models.ResourceMixin,
    orm.SQLStorableMixin,
):
    """A PaaS type registered in the metapaas control plane.

    ``name`` is the PaaS slug (e.g. ``"s3"``); ``element_name`` is the exordos
    element name under which clients reference this PaaS (e.g. ``"s3aas"`` →
    clients use ``$s3aas.types.s3.instances``). The metapaas core-agent uses
    ``element_name`` to subscribe to ``em_<element_name>_types_<name>_*`` kinds.

    ``package`` is any pip spec; ``index_url`` optionally points pip at a private
    index. The PluginReconciler installs the package so the plugin's
    ``PaaSDefinition`` entry-point becomes discoverable without a metapaas rebuild.
    """

    __tablename__ = "metapaas_paas_types"

    # Element name under which clients see this PaaS (e.g. "s3aas").
    element_name = properties.property(types.String(min_length=1, max_length=128))
    # Pip distribution name or a wheel/sdist URL.
    package = properties.property(types.String(min_length=1, max_length=512))
    # Optional version pin (applied as ``package==version`` for index installs).
    version = properties.property(types.String(max_length=128), default="")
    # Optional pip --index-url.
    index_url = properties.property(types.String(max_length=512), default="")
    status = properties.property(
        types.String(max_length=32),
        default="NEW",
    )
