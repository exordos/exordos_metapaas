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

"""Tests for offline specification generation and element packaging."""

import os
from pathlib import Path
import sys
from unittest import mock

import yaml

from exordos_metapaas.cmd import openapi_spec
from exordos_metapaas.common import openapi
from exordos_metapaas.user_api.api import routes


def test_build_describes_runtime_without_discovering_plugins() -> None:
    original = routes.TypeRoute.get_routes
    with mock.patch.object(routes, "discover_paas", side_effect=AssertionError):
        spec = openapi.build()
    assert routes.TypeRoute.get_routes == original
    assert spec["openapi"] == "3.0.3"
    assert spec["info"]["version"] == "latest"
    assert spec["servers"][0]["url"] == "http://127.0.0.1:8080"
    assert {path for path in spec["paths"] if path.startswith("/v1/")} == {
        "/v1/",
        "/v1/types/",
        "/v1/types/{PaaSTypeUuid}",
    }
    assert set(spec["paths"]["/v1/types/"]) == {"get", "post"}
    assert "PaaSType_Get" in spec["components"]["schemas"]

    def check_references(node: object) -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                target = spec
                assert node["$ref"].startswith("#/")
                for part in node["$ref"][2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            for value in node.values():
                check_references(value)
        elif isinstance(node, list):
            for value in node:
                check_references(value)

    check_references(spec)


def test_main_writes_spec_without_changing_description_text(tmp_path: Path) -> None:
    spec = openapi.build()
    with (
        mock.patch.object(sys, "argv", ["openapi-spec", "-o", str(tmp_path)]),
        mock.patch.object(openapi, "build", return_value=spec),
    ):
        openapi_spec.main()
    assert yaml.safe_load((tmp_path / "openapi_user.yaml").read_text()) == spec


def test_element_build_generates_executable_openapi_artifact() -> None:
    root = Path(__file__).parents[3]
    config = yaml.safe_load((root / "exordos/exordos.yaml").read_text())
    artifact = config["build"]["elements"][0]["artifacts"][0]
    script = root / "exordos" / artifact["script"]
    assert script.is_file()
    assert os.access(script, os.X_OK)
    assert artifact["artifacts"] == ["../docs/openapi/openapi_user.yaml"]
