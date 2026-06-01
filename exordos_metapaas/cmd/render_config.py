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
"""Registry-driven rendering of the metapaas service configs.

The metapaas runtime hosts an open-ended set of PaaS plugins, so the gservice
``[launchpad]`` services list and the core-agent ``[models]``/``[filters]`` map
must not be hardcoded for any single PaaS. This command discovers all installed
``PaaSDefinition``\\s (``exordos_metapaas_paas`` entry points) and generates both
config files from the registry plus the node environment.

Run at first boot, and again by the runtime plugin installer after a new plugin
is pip-installed (then restart the services) — adding a PaaS needs no rebuild
and no edits to any baked-in config.

Credentials come from the environment (the config injected via
``/etc/exordos_init.txt`` plus the locally generated DB password). On a
re-render the DB password is recovered from the existing gservice config so it
stays stable across plugin installs.
"""
from __future__ import annotations

import argparse
import configparser
import logging
import os
import sys

from exordos_metapaas.registry import discover_paas

LOG = logging.getLogger(__name__)

DEFAULT_GSERVICE_CONF = "/etc/exordos_metapaas/exordos_metapaas.conf"
DEFAULT_CORE_AGENT_CONF = "/etc/exordos_metapaas/core_agent.conf"

# Core endpoints used by the db-back core-agent and the gservice UAgent.
CORE_API_BASE_URL = "http://core.local.genesis-core.tech:11010"
CORE_ORCH_ENDPOINT = "http://core.local.genesis-core.tech:11011"
CORE_STATUS_ENDPOINT = "http://core.local.genesis-core.tech:11012"

WORK_DIR = "/var/lib/exordos/exordos_metapaas"

def _agent_kind(element_name, slug, subpath):
    """Build the EM kind string for a PaaS plugin resource.

    Format: ``em_<element_name>_types_<slug>_<subpath>`` where subpath dots are
    replaced by underscores. Matches the kind the element-manager emits for
    ``$<element_name>.types.<slug>.<subpath>`` (Core Resource.kind).
    """
    return "em_{element}_types_{slug}_{sub}".format(
        element=element_name, slug=slug, sub=subpath.replace(".", "_")
    )


def _connection_url(env):
    return "postgresql://{user}:{pwd}@localhost:5432/{db}".format(
        user=env["GC_PG_USER"], pwd=env["GC_PG_PASS"], db=env["GC_PG_DB"]
    )


def _recover_pg_password(path):
    """Recover the DB password from an already-rendered gservice config."""
    if not os.path.isfile(path):
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read(path)
        url = parser.get("db", "connection_url")
    except (configparser.Error, KeyError):
        return None
    # postgresql://user:PASSWORD@host:port/db
    try:
        creds = url.split("://", 1)[1].split("@", 1)[0]
        return creds.split(":", 1)[1]
    except IndexError:
        return None


def _recover_from_config(path):
    """Recover stable values from an already-rendered gservice config."""
    if not os.path.isfile(path):
        return {}
    parser = configparser.ConfigParser()
    try:
        parser.read(path)
    except configparser.Error:
        return {}
    recovered = {}
    try:
        recovered["GC_PG_PASS"] = _recover_pg_password(path)
    except Exception:
        pass
    try:
        recovered["PROJECT_ID"] = parser.get("core", "project_id")
    except (configparser.Error, KeyError):
        pass
    try:
        recovered["IAM_USER_NAME"] = parser.get("core", "core_username")
        recovered["IAM_USER_PASS"] = parser.get("core", "core_password")
    except (configparser.Error, KeyError):
        pass
    try:
        recovered["GC_HS256_JWKS_ENCRYPTION_KEY"] = parser.get(
            "iam", "hs256_jwks_decryption_key"
        )
    except (configparser.Error, KeyError):
        pass
    return {k: v for k, v in recovered.items() if v}


def _collect_env(gservice_conf):
    env = {
        "IAM_USER_NAME": os.environ.get("IAM_USER_NAME", "metapaas"),
        "IAM_USER_PASS": os.environ.get("IAM_USER_PASS", "metapaas"),
        "PROJECT_ID": os.environ.get("PROJECT_ID", ""),
        "GC_HS256_JWKS_ENCRYPTION_KEY": os.environ.get(
            "GC_HS256_JWKS_ENCRYPTION_KEY", ""
        ),
        "GC_PG_USER": os.environ.get("GC_PG_USER", "metapaas"),
        "GC_PG_DB": os.environ.get("GC_PG_DB", "metapaas"),
        "GC_PG_PASS": os.environ.get("GC_PG_PASS", ""),
    }
    # Stable values (DB password, project ID, credentials) are written once at
    # first boot and recovered from the existing config when env vars are absent
    # (e.g. when render_config runs from a systemd service without
    # /etc/exordos_init.txt sourced). Env vars always take precedence — only
    # fall back to the config when the env value is empty or a known default.
    defaults = {"IAM_USER_NAME": "metapaas", "IAM_USER_PASS": "metapaas"}
    for key, value in _recover_from_config(gservice_conf).items():
        if value and (not env.get(key) or env.get(key) == defaults.get(key, "")):
            env[key] = value
    return env


def render_gservice_conf(env):
    # Builder services are instantiated dynamically from the plugin registry at
    # runtime (cmd/gservice.py calls discover_paas() and definition.get_builders()).
    # No per-plugin config sections needed here.
    lines = [
        "# Generated by exordos-metapaas-render-config from the PaaS registry.",
        "# Do not edit by hand; re-run render-config after installing a plugin.",
        "",
        "[db]",
        "connection_url = " + _connection_url(env),
        "",
        "[logging]",
        "config = logging.yaml",
        "",
        "[iam]",
        "hs256_jwks_decryption_key = " + env["GC_HS256_JWKS_ENCRYPTION_KEY"],
        "",
        "[HS256]",
        "",
        "[user_api]",
        "bind_host = 0.0.0.0",
        "",
        "[status_api]",
        "bind_host = 0.0.0.0",
        "",
        "[orch_api]",
        "bind_host = 0.0.0.0",
        "",
        "[core]",
        "core_username = " + env["IAM_USER_NAME"],
        "core_password = " + env["IAM_USER_PASS"],
        "core_api_base_url = " + CORE_API_BASE_URL,
        "project_id = " + env["PROJECT_ID"],
        "",
    ]
    return "\n".join(lines) + "\n"


def render_core_agent_conf(env, definitions):
    # Built-in: the PaaS type registry itself. Always subscribed so the
    # PluginReconciler gets PaaSType rows regardless of installed plugins.
    models = {
        "em_metapaas_types": "exordos_metapaas.dm.models:PaaSType",
    }
    filters = {
        "em_metapaas_types": "project_id:{project}".format(
            project=env["PROJECT_ID"]
        ),
    }
    for definition in definitions.values():
        slug = definition.slug
        element = definition.element_name or slug
        for subpath, model in definition.get_agent_models().items():
            models[_agent_kind(element, slug, subpath)] = model
        for subpath, field in definition.get_agent_filters().items():
            filters[_agent_kind(element, slug, subpath)] = (
                "{field}:{project}".format(field=field, project=env["PROJECT_ID"])
            )

    lines = [
        "# Generated by exordos-metapaas-render-config from the PaaS registry.",
        "# Do not edit by hand; re-run render-config after installing a plugin.",
        "",
        "[DEFAULT]",
        "verbose = True",
        "debug = True",
        "",
        "[db]",
        "connection_url = " + _connection_url(env),
        "connection_pool_max_size = 4",
        "",
        "[agent]",
        "uuid5_name = core_agent",
        "payload_path = " + os.path.join(WORK_DIR, "core_agent/payload.json"),
        "target_fields_path = "
        + os.path.join(WORK_DIR, "core_agent/target_fields.json"),
        "orch_client = http",
        "orch_endpoint = " + CORE_ORCH_ENDPOINT,
        "status_endpoint = " + CORE_STATUS_ENDPOINT,
        "",
        "[models]",
    ]
    for kind, model in models.items():
        lines.append("{kind} = {model}".format(kind=kind, model=model))
    lines += ["", "[filters]"]
    for kind, value in filters.items():
        lines.append("{kind} = {value}".format(kind=kind, value=value))
    return "\n".join(lines) + "\n"


def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gservice-out", default=DEFAULT_GSERVICE_CONF)
    parser.add_argument("--core-agent-out", default=DEFAULT_CORE_AGENT_CONF)
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print rendered configs to stdout instead of writing files.",
    )
    args = parser.parse_args()

    definitions = discover_paas()
    env = _collect_env(args.gservice_out)
    if not env["GC_PG_PASS"]:
        LOG.error(
            "DB password not in environment and not recoverable from %s",
            args.gservice_out,
        )
        return 1

    gservice = render_gservice_conf(env)
    core_agent = render_core_agent_conf(env, definitions)

    LOG.info(
        "Rendered configs for %d PaaS plugin(s): %s",
        len(definitions),
        ", ".join(sorted(definitions)) or "(none)",
    )

    if args.print:
        sys.stdout.write("# ===== %s =====\n" % args.gservice_out)
        sys.stdout.write(gservice)
        sys.stdout.write("\n# ===== %s =====\n" % args.core_agent_out)
        sys.stdout.write(core_agent)
        return 0

    with open(args.gservice_out, "w") as fh:
        fh.write(gservice)
    with open(args.core_agent_out, "w") as fh:
        fh.write(core_agent)
    LOG.info("Wrote %s and %s", args.gservice_out, args.core_agent_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
