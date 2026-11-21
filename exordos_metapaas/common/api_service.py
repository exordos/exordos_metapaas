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

import functools

from gcl_looper.services import hub
from oslo_config import cfg

reload_opts = [
    cfg.BoolOpt(
        "autoreload", default=False, help="Automatically reload API on source changes"
    ),
    cfg.ListOpt(
        "reload-dirs",
        default=[],
        help="Additional source directories to watch in autoreload mode",
    ),
]


def start(factory, argv, options, extra_services=()):
    service = hub.ReloadableProcessHubService(
        autoreload=options.autoreload, reload_dirs=options.reload_dirs
    )
    for _ in range(options.workers):
        service.add_service_factory(functools.partial(factory, argv))
    for extra_service in extra_services:
        service.add_service(extra_service)
    service.start()
