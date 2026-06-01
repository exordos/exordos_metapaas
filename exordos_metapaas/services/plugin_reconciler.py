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
"""Declarative runtime installation of PaaS plugins.

A PaaS provider manifest declares ``$metapaas.plugins`` resources; the db-back
core-agent reconciles them into the ``metapaas_plugins`` table. This service
watches that table and, for any plugin whose package is not yet present in the
runtime, pip-installs it (``exordos-metapaas-install-paas``) — applying its
migrations, regenerating the registry-driven configs and restarting the
services so its routes/builders/models go live. No metapaas rebuild required.

This is the metapaas-side counterpart that makes a PaaS truly live in its own
package/repository: only this reconciler is baked into the metapaas image, the
PaaS code arrives at runtime from pip.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import typing as tp

from gcl_looper.services import basic as looper_basic
from restalchemy.dm import filters as ra_filters

from exordos_metapaas.dm import models as dm_models

LOG = logging.getLogger(__name__)

STATUS_ACTIVE = "ACTIVE"

# Services with stateful startup that must be restarted to pick up a new plugin.
# user_api reloads routes via SIGUSR1 without restart; status_api/orch_api serve
# gcl_sdk generic routes and need no action.
_RESTART_SERVICES = (
    "exordos-metapaas-gservice",
    "exordos-metapaas-core-agent",
)

_USER_API_SERVICE = "exordos-metapaas-user-api"

_DISCOVER_SNIPPET = (
    "import json;"
    "from exordos_metapaas.registry import discover_paas;"
    "print(json.dumps(sorted(discover_paas())))"
)


class PluginReconciler(looper_basic.BasicService):
    """Reconcile desired PaaS plugins (metapaas_plugins) into pip installs."""

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _installed_slugs() -> tp.Set[str]:
        """Discover installed PaaS slugs in a fresh interpreter."""
        try:
            out = subprocess.check_output([sys.executable, "-c", _DISCOVER_SNIPPET])
            return set(json.loads(out.decode()))
        except (subprocess.CalledProcessError, ValueError):
            LOG.exception("Failed to discover installed PaaS plugins")
            return set()

    @staticmethod
    def _spec(plugin) -> str:
        if plugin.version and not _looks_like_url(plugin.package):
            return f"{plugin.package}=={plugin.version}"
        return plugin.package

    def _install(self, plugin) -> bool:
        cmd = [
            "exordos-metapaas-install-paas",
            self._spec(plugin),
            "--no-restart",
        ]
        if plugin.index_url:
            cmd += ["--index-url", plugin.index_url]
        LOG.info("Installing plugin %s: %s", plugin.name, " ".join(cmd))
        try:
            subprocess.check_call(cmd)
            return True
        except subprocess.CalledProcessError:
            LOG.exception("Install failed for plugin %s", plugin.name)
            return False

    @staticmethod
    def _signal_user_api():
        """Send SIGUSR1 to user_api workers so they reload plugin routes."""
        LOG.info("Signaling %s to reload plugin routes (SIGUSR1)", _USER_API_SERVICE)
        subprocess.run(
            ["systemctl", "kill", "--kill-who=all", "-s", "SIGUSR1", _USER_API_SERVICE],
            check=False,
        )

    @staticmethod
    def _restart_detached():
        # Restart via a transient systemd unit so it survives this gservice
        # being restarted as part of the set.
        LOG.info("Restarting services (detached) to load new plugin(s)")
        subprocess.run(
            ["systemd-run", "--no-block", "systemctl", "restart", *_RESTART_SERVICES],
            check=False,
        )

    # -- main loop ----------------------------------------------------------

    def _iteration(self):
        pending = dm_models.PaaSType.objects.get_all(
            filters={"status": ra_filters.NE(STATUS_ACTIVE)},
        )
        if not pending:
            return

        installed = self._installed_slugs()

        # PaaS types that became discoverable (installed + restarted): mark ACTIVE.
        to_install = []
        for plugin in pending:
            # PaaSType.name is the slug (e.g. "s3"); that's what discover_paas() keys on.
            if plugin.name in installed:
                plugin.status = STATUS_ACTIVE
                plugin.save()
                LOG.info("Plugin %s is active", plugin.name)
            else:
                to_install.append(plugin)

        if not to_install:
            return

        installed_any = False
        for plugin in to_install:
            if self._install(plugin):
                installed_any = True

        # Activation requires a restart so the long-running services pick up the
        # new routes/builders/models. The next iteration marks them ACTIVE.
        if installed_any:
            self._signal_user_api()
            self._restart_detached()


def _looks_like_url(package: str) -> bool:
    return "://" in package or package.endswith((".whl", ".tar.gz"))
