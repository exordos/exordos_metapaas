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

from gcl_sdk.audit import opts
from oslo_config import cfg
import pytest
import yaml

ROOT = Path(__file__).parents[3]


@pytest.mark.parametrize("enabled", [False, True])
def test_api_starts_sender_only_when_enabled(enabled: bool) -> None:
    with mock.patch.object(cfg, "CONF", cfg.ConfigOpts()):
        from exordos_metapaas.cmd import user_api
    conf = mock.MagicMock()
    conf.__getitem__.return_value.workers = 1
    with (
        mock.patch.object(user_api, "CONF", conf),
        mock.patch.object(user_api.config, "parse"),
        mock.patch.object(user_api.infra_log, "configure"),
        mock.patch.object(user_api.app, "build_wsgi_application"),
        mock.patch.object(user_api.drivers, "HttpDriver"),
        mock.patch.object(user_api.bjoern_service, "BjoernService"),
        mock.patch.object(user_api.hub, "ProcessHubService") as hub,
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
        ) as database,
    ):
        user_api.main()
        if enabled:
            sender = build.return_value
            hub.return_value.add_service.assert_any_call(sender)
            database.assert_not_called()
            sender.add_setup.call_args.args[0]()
            database.assert_called_once_with(conf=conf)
        else:
            build.assert_not_called()
        hub.return_value.start.assert_called_once()


def test_optional_manifest_config_and_token_scope(tmp_path: Path) -> None:
    text = (ROOT / "exordos/manifests/metapaas_audit.yaml.j2").read_text()
    manifest = yaml.safe_load(text.replace("{{ version }}", "test"))
    resources = manifest["resources"]
    assert "audit" in manifest["requirements"]
    main = yaml.safe_load((ROOT / "exordos/manifests/metapaas.yaml.j2").read_text())
    assert "audit" not in main.get("requirements", {})
    config = resources["$core.config.configs"]["metapaas_audit_config"]
    assert config["project_id"] == "12345678-c625-4fee-81d5-f691897b8142"
    assert config["path"] == "/etc/exordos_metapaas/exordos_metapaas.d/audit.conf"
    assert config["mode"] == "0600"
    token = resources["$core.iam.tokens"]["metapaas_audit_token"]
    assert token["auto_renew"] is True
    assert (
        token["scope"] == 'f"project:{$metapaas_audit.imports.$metapaas_project:uuid}"'
    )
    content = config["body"]["content"].strip().removeprefix('f"').removesuffix('"')
    content = content.replace(
        "{{ audit_endpoint | default('http://audit.local.genesis-core.tech:8080/') }}",
        "http://audit.example/",
    ).replace("{$core.iam.tokens.$metapaas_audit_token:access_token}", "test-token")
    path = tmp_path / "audit.conf"
    path.write_text(content)
    conf = cfg.ConfigOpts()
    opts.register_audit_delivery_opts(conf)
    conf(["--config-file", str(path)])
    assert conf.audit_delivery.enabled is True
    assert conf.audit_delivery.auth_token == "test-token"
    assert conf.audit_delivery.endpoint == "http://audit.example/"
