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

from exordos_metapaas.registry import PaaSDefinition
from exordos_metapaas.registry import discover_paas


class TestPaaSDefinitionDefaults:
    def test_slug_empty(self) -> None:
        assert PaaSDefinition.slug == ""

    def test_element_name_empty(self) -> None:
        assert PaaSDefinition.element_name == ""

    def test_get_migrations_path_none(self) -> None:
        assert PaaSDefinition().get_migrations_path() is None

    def test_get_builders_empty(self) -> None:
        assert PaaSDefinition().get_builders("u", "p", "url", "proj") == []

    def test_get_agent_models_empty(self) -> None:
        assert PaaSDefinition().get_agent_models() == {}

    def test_get_agent_filters_empty(self) -> None:
        assert PaaSDefinition().get_agent_filters() == {}


def _make_stub_ep(name: str = "stub", slug: str = "stub"):
    """Build an EntryPoint mock loading an inline PaaSDefinition subclass.

    The runtime discovers plugins purely via entry-points, so these tests use a
    local stub rather than any concrete plugin — metapaas_demo is a separate
    package and its conformance is covered by its own test suite.
    """
    import importlib.metadata as im
    from unittest.mock import MagicMock

    ep = MagicMock(spec=im.EntryPoint)
    ep.name = name
    ep.load.return_value = type("StubDefinition", (PaaSDefinition,), {"slug": slug})
    return ep


class TestDiscoverPaas:
    def test_finds_plugin(self) -> None:
        import importlib.metadata as im
        from unittest.mock import patch

        with patch.object(
            im, "entry_points", return_value=[_make_stub_ep(slug="demo")]
        ):
            result = discover_paas()
        assert "demo" in result

    def test_slug_matches_key(self) -> None:
        import importlib.metadata as im
        from unittest.mock import patch

        with patch.object(
            im, "entry_points", return_value=[_make_stub_ep(slug="demo")]
        ):
            result = discover_paas()
        assert result["demo"].slug == "demo"

    def test_duplicate_slug_raises(self) -> None:
        import importlib.metadata as im
        from unittest.mock import MagicMock
        from unittest.mock import patch

        ep1 = MagicMock()
        ep1.name = "ep1"
        ep1.load.return_value = type("Dup", (PaaSDefinition,), {"slug": "clash"})
        ep2 = MagicMock()
        ep2.name = "ep2"
        ep2.load.return_value = type("Dup2", (PaaSDefinition,), {"slug": "clash"})

        with patch.object(im, "entry_points", return_value=[ep1, ep2]):
            try:
                discover_paas()
                assert False, "expected ValueError"
            except ValueError as exc:
                assert "clash" in str(exc)

    def test_no_slug_skipped(self) -> None:
        import importlib.metadata as im
        from unittest.mock import MagicMock
        from unittest.mock import patch

        ep = MagicMock()
        ep.name = "bad"
        ep.load.return_value = type("NoSlug", (PaaSDefinition,), {"slug": ""})

        with patch.object(im, "entry_points", return_value=[ep]):
            result = discover_paas()
        assert result == {}
