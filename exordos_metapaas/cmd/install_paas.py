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
"""Install a PaaS plugin into a running metapaas control plane — no rebuild.

A PaaS plugin is an ordinary Python distribution registering a
``PaaSDefinition`` (``exordos_metapaas_paas`` entry point). Adding a new PaaS to
a live metapaas node is therefore: pip-install the package, apply its
migrations, regenerate the registry-driven configs, and restart the services so
they pick up the new routes / builders / models. This command does exactly that,
idempotently.

Specs are anything pip understands: ``name==1.2.3`` from an index, a ``.whl`` /
``.tar.gz`` path, or a URL. Example::

    exordos-metapaas-install-paas exordos_paas_mail==0.1.0
    exordos-metapaas-install-paas /tmp/exordos_paas_mail-0.1.0-py3-none-any.whl

It is the imperative core the runtime reconciler invokes when a desired-plugin
resource appears (delivered via the element manager / a Config), so installs can
be driven declaratively too.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys

LOG = logging.getLogger(__name__)

CONFIG_DIR = "/etc/exordos_metapaas/"
GSERVICE_CONF = "/etc/exordos_metapaas/exordos_metapaas.conf"
CORE_AGENT_CONF = "/etc/exordos_metapaas/core_agent.conf"

# Services with stateful startup (builder lists, config read at boot) that must
# be restarted to pick up a new plugin. user_api reloads routes via SIGUSR1
# without restart; status_api/orch_api serve gcl_sdk generic routes and need
# no action at all.
_RESTART_SERVICES = (
    "exordos-metapaas-gservice",
    "exordos-metapaas-core-agent",
)

# user_api workers reload their plugin route cache on SIGUSR1 without restart.
_USER_API_SERVICE = "exordos-metapaas-user-api"

# Snippet run in a fresh interpreter so it sees entry points registered by a
# package installed earlier in this same process.
_DISCOVER_SNIPPET = (
    "import json;"
    "from exordos_metapaas.registry import discover_paas;"
    "print(json.dumps("
    "{s: d.get_migrations_path() for s, d in discover_paas().items()}))"
)


def _discover_plugins():
    """Return ``{slug: migrations_path_or_None}`` from a fresh interpreter."""
    out = subprocess.check_output([sys.executable, "-c", _DISCOVER_SNIPPET])
    return json.loads(out.decode())


def _pip_install(specs, index_url, upgrade):
    cmd = ["uv", "pip", "install", "--python", sys.executable]
    if upgrade:
        cmd.append("--upgrade")
    if index_url:
        cmd += ["--extra-index-url", index_url]
    cmd += list(specs)
    LOG.info("pip install: %s", " ".join(cmd))
    subprocess.check_call(cmd)


def _apply_migration(path):
    LOG.info("Applying migrations: %s", path)
    subprocess.check_call(
        ["ra-apply-migration", "--config-dir", CONFIG_DIR, "--path", path]
    )


def _render_config():
    LOG.info("Re-rendering registry-driven configs")
    subprocess.check_call(
        [
            "exordos-metapaas-render-config",
            "--gservice-out",
            GSERVICE_CONF,
            "--core-agent-out",
            CORE_AGENT_CONF,
        ]
    )


def _signal_user_api():
    """Send SIGUSR1 to all user_api workers so they reload plugin routes."""
    LOG.info("Signaling %s to reload plugin routes (SIGUSR1)", _USER_API_SERVICE)
    subprocess.run(
        ["systemctl", "kill", "--kill-who=all", "-s", "SIGUSR1", _USER_API_SERVICE],
        check=False,
    )


def _restart_services():
    LOG.info("Restarting metapaas services: %s", " ".join(_RESTART_SERVICES))
    subprocess.check_call(["systemctl", "restart", *_RESTART_SERVICES])


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("specs", nargs="+", help="pip package specs to install")
    parser.add_argument("--index-url", default=None, help="pip index URL")
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="pass --upgrade to pip (for updating an installed plugin)",
    )
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="skip restarting services (e.g. when staging several installs)",
    )
    args = parser.parse_args()

    before = _discover_plugins()
    LOG.info("Installed PaaS before: %s", ", ".join(sorted(before)) or "(none)")

    _pip_install(args.specs, args.index_url, args.upgrade)

    after = _discover_plugins()
    added = sorted(set(after) - set(before))
    LOG.info(
        "Installed PaaS after: %s (added: %s)",
        ", ".join(sorted(after)) or "(none)",
        ", ".join(added) or "(none, upgrade-only)",
    )

    # Apply migrations for newly added plugins (idempotent for upgrades:
    # ra-apply-migration applies only un-applied steps tracked in ra_migrations).
    targets = added or sorted(after)
    for slug in targets:
        path = after.get(slug)
        if path:
            _apply_migration(path)

    _render_config()

    if args.no_restart:
        LOG.info("Skipping service restart (--no-restart).")
    else:
        _signal_user_api()
        _restart_services()

    LOG.info("PaaS install complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
