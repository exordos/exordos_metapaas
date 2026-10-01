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

import configparser

from exordos_metapaas.cmd import render_config


class TestRenderGserviceConf:
    def test_iam_endpoint_is_default_client(self) -> None:
        env = render_config._collect_env("/nonexistent")
        parser = configparser.ConfigParser()
        parser.read_string(render_config.render_gservice_conf(env))

        assert parser.get("iam", "iam_endpoint") == (
            "http://core.local.genesis-core.tech:80/api/core/v1/iam/clients/default"
        )
