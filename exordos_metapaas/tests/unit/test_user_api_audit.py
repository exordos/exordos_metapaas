#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

from pathlib import Path
from unittest import mock

from oslo_config import cfg
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("enabled", [False, True])
def test_api_starts_audit_sender_only_when_enabled(enabled: bool) -> None:
    with mock.patch.object(cfg, "CONF", cfg.ConfigOpts()):
        from exordos_metapaas.cmd import user_api

    conf = mock.MagicMock()
    conf.__getitem__.return_value.workers = 1
    with (
        mock.patch.object(user_api, "CONF", conf),
        mock.patch.object(user_api.config, "parse"),
        mock.patch.object(user_api.infra_log, "configure"),
        mock.patch.object(user_api.app, "build_wsgi_application"),
        mock.patch.object(user_api.bjoern_service, "BjoernService"),
        mock.patch.object(user_api.hub, "ProcessHubService") as service_hub,
        mock.patch.object(
            user_api.audit_opts,
            "get_audit_delivery_config",
            return_value=mock.Mock(enabled=enabled),
        ),
        mock.patch.object(
            user_api.audit_senders.AuditSenderService, "build_from_config"
        ) as build,
        mock.patch.object(
            user_api.engines.engine_factory, "configure_postgresql_factory"
        ) as configure_database,
    ):
        user_api.main()
        if enabled:
            sender = build.return_value
            service_hub.return_value.add_service.assert_any_call(sender)
            configure_database.assert_not_called()
            sender.add_setup.call_args.args[0]()
            configure_database.assert_called_once_with(conf=conf)
        else:
            build.assert_not_called()
        service_hub.return_value.start.assert_called_once()


def test_optional_audit_manifest_config_and_token_scope() -> None:
    path = ROOT / "exordos/manifests/metapaas_audit.yaml.j2"
    manifest = yaml.safe_load(path.read_text().replace("{{ version }}", "test"))

    assert "audit" in manifest["requirements"]
    assert "metapaas" in manifest["requirements"]
    resources = manifest["resources"]
    config = resources["$core.config.configs"]["metapaas_audit_config"]
    token = resources["$core.iam.tokens"]["metapaas_audit_token"]
    assert config["path"] == "/etc/exordos_metapaas/exordos_metapaas.d/audit.conf"
    assert token["auto_renew"] is True
    assert token["scope"] == (
        'f"project:{$metapaas_audit.imports.$metapaas_project:uuid}"'
    )
    assert "enabled = true" in config["body"]["content"]
