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

_DISCOVER_SNIPPET = """\
import json
import importlib.metadata as im
from exordos_metapaas.registry import discover_paas
discovered = discover_paas()
slug_ver = {}
for ep in im.entry_points(group='exordos_metapaas_paas'):
    obj = ep.load()
    inst = obj() if isinstance(obj, type) else obj
    slug = getattr(inst, 'slug', '')
    if slug and slug in discovered:
        slug_ver[slug] = ep.dist.version if ep.dist else ''
print(json.dumps(slug_ver))
"""


class PluginReconciler(looper_basic.BasicService):
    """Reconcile desired PaaS plugins (metapaas_plugins) into pip installs."""

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _installed_slugs() -> tp.Dict[str, str]:
        """Return {slug: dist_version} for all installed PaaS plugins.

        Runs in a fresh interpreter so entry points registered by a package
        installed earlier in this same process are visible.
        """
        try:
            out = subprocess.check_output([sys.executable, "-c", _DISCOVER_SNIPPET])
            return json.loads(out.decode())
        except (subprocess.CalledProcessError, ValueError):
            LOG.exception("Failed to discover installed PaaS plugins")
            return {}

    @staticmethod
    def _spec(plugin) -> str:
        if plugin.version and not _looks_like_url(plugin.package):
            return f"{plugin.package}=={plugin.version}"
        return plugin.package

    @staticmethod
    def _should_reinstall(plugin, installed_version: str) -> bool:
        """Return True if the installed plugin does not match the desired spec.

        For URL/whl/tar.gz packages the dist version cannot be compared against
        a URL, so we always reinstall (triggered only when ``package`` changed
        and ``update()`` reset the status to NEW).  For named packages with a
        version pin we compare the dist version directly.
        """
        if _looks_like_url(plugin.package):
            return True
        if plugin.version:
            return installed_version != plugin.version
        return False

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

        to_install = []
        newly_activated = []
        for plugin in pending:
            if plugin.name in installed:
                if self._should_reinstall(plugin, installed[plugin.name]):
                    LOG.info(
                        "Plugin %s needs upgrade: installed=%s, desired=%s",
                        plugin.name,
                        installed[plugin.name],
                        plugin.version or plugin.package,
                    )
                    to_install.append(plugin)
                else:
                    plugin.status = STATUS_ACTIVE
                    plugin.save()
                    newly_activated.append(plugin.name)
                    LOG.info("Plugin %s is active", plugin.name)
            else:
                to_install.append(plugin)

        if not to_install and not newly_activated:
            return

        installed_any = False
        for plugin in to_install:
            if self._install(plugin):
                # Mark ACTIVE immediately so URL-based packages don't reinstall
                # on the next gservice startup before routes/services reload.
                plugin.status = STATUS_ACTIVE
                plugin.save()
                installed_any = True
                LOG.info("Plugin %s installed and marked active", plugin.name)

        if installed_any or newly_activated:
            self._signal_user_api()
            self._restart_detached()


def _looks_like_url(package: str) -> bool:
    return "://" in package or package.endswith((".whl", ".tar.gz"))
