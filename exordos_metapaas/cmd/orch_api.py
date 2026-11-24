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

from oslo_config import cfg
from restalchemy.common import config_opts as ra_config_opts

from exordos_metapaas.common import api_service
from exordos_metapaas.common import config
from exordos_metapaas.common import log as infra_log

api_cli_opts = [
    cfg.StrOpt(
        "bind-host",
        default="127.0.0.1",
        help="The host IP to bind to",
    ),
    cfg.IntOpt(
        "bind-port",
        default=11011,
        help="The port to bind to",
    ),
    cfg.IntOpt(
        "workers",
        default=1,
        min=1,
        help="How many http servers should be started",
    ),
]


DOMAIN = "orch_api"

CONF = cfg.CONF
CONF.register_cli_opts(api_cli_opts + api_service.reload_opts, DOMAIN)
ra_config_opts.register_posgresql_db_opts(CONF)


def _build_worker(argv):
    """Preload an API service in the generation before workers fork."""
    from gcl_looper.services import bjoern_service
    from restalchemy.storage.sql import engines

    from exordos_metapaas.orch_api.api import app

    config.parse(argv)
    infra_log.configure()
    service = bjoern_service.BjoernService(
        wsgi_app=app.build_wsgi_application(),
        host=CONF[DOMAIN].bind_host,
        port=CONF[DOMAIN].bind_port,
        bjoern_kwargs={"reuse_port": True},
    )
    service.add_setup(
        lambda: engines.engine_factory.configure_postgresql_factory(conf=CONF)
    )
    return service


def main():
    # Parse config
    config.parse(sys.argv[1:])

    # Configure logging
    infra_log.configure()
    log = logging.getLogger(__name__)

    api_service.start(_build_worker, sys.argv[1:], CONF[DOMAIN])

    log.info("Bye!!!")


if __name__ == "__main__":
    main()
