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
import sys
import uuid as sys_uuid

from gcl_looper.services.oslo import launchpad as lp_lib
from oslo_config import cfg
from restalchemy.common import config_opts as ra_config_opts
from restalchemy.storage.sql import engines

from exordos_metapaas.common import log as infra_log
from exordos_metapaas.registry import discover_paas
from exordos_metapaas.services.gservice import InfraScheduler, UAgent
from exordos_metapaas.services.plugin_reconciler import PluginReconciler

CONF = cfg.CONF

ra_config_opts.register_posgresql_db_opts(CONF)

CONF.register_opts(
    [
        cfg.StrOpt("core_username", default="metapaas"),
        cfg.StrOpt("core_password", default="metapaas"),
        cfg.StrOpt(
            "core_api_base_url",
            default="http://core.local.genesis-core.tech:11010",
        ),
        cfg.StrOpt("project_id"),
    ],
    group="core",
)

CONF.register_opts(
    [
        cfg.FloatOpt("iter_min_period", default=3),
        cfg.FloatOpt("iter_pause", default=0.1),
    ],
    group="gservice",
)


def main():
    log = logging.getLogger(__name__)

    CONF(sys.argv[1:], project="exordos_metapaas_gservice")
    infra_log.configure()
    engines.engine_factory.configure_postgresql_factory(CONF)

    core_kwargs = {
        "core_username": CONF.core.core_username,
        "core_password": CONF.core.core_password,
        "core_api_base_url": CONF.core.core_api_base_url,
        "project_id": sys_uuid.UUID(CONF.core.project_id),
    }

    services = [
        InfraScheduler(),
        UAgent(**core_kwargs),
        PluginReconciler(),
    ]

    for definition in discover_paas().values():
        services.extend(definition.get_builders(**core_kwargs))

    lp_lib.LaunchpadService(
        services=services,
        iter_min_period=CONF.gservice.iter_min_period,
        iter_pause=CONF.gservice.iter_pause,
    ).start()

    log.info("Bye!!!")


if __name__ == "__main__":
    main()
