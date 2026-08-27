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

from exordos_metapaas.services.plugin_reconciler import PluginReconciler
from exordos_metapaas.services.plugin_reconciler import _is_urn
from exordos_metapaas.services.plugin_reconciler import _looks_like_url


class _FakePlugin:
    def __init__(self, package, version="", index_url="", name="fake"):
        self.package = package
        self.version = version
        self.index_url = index_url
        self.name = name


def _make_reconciler() -> PluginReconciler:
    """Build a PluginReconciler with dummy creds (no network calls in __init__)."""
    return PluginReconciler(
        core_api_base_url="http://fake",
        core_username="user",
        core_password="pass",
    )


class TestIsUrn:
    def test_urn_prefix(self) -> None:
        assert _is_urn("urn:artifacts:abc-123") is True

    def test_plain_name(self) -> None:
        assert _is_urn("mypkg") is False

    def test_url(self) -> None:
        assert _is_urn("http://repo/pkg.whl") is False


class TestLooksLikeUrl:
    def test_http_url(self) -> None:
        assert _looks_like_url("http://example.com/pkg.whl") is True

    def test_https_url(self) -> None:
        assert _looks_like_url("https://example.com/pkg.whl") is True

    def test_whl_suffix(self) -> None:
        assert _looks_like_url("pkg-1.0-py3-none-any.whl") is True

    def test_tar_gz_suffix(self) -> None:
        assert _looks_like_url("pkg-1.0.tar.gz") is True

    def test_plain_name(self) -> None:
        assert _looks_like_url("mypkg") is False

    def test_name_with_version_constraint(self) -> None:
        assert _looks_like_url("mypkg>=1.0") is False


class TestSpec:
    def test_name_and_version_pinned(self) -> None:
        r = _make_reconciler()
        assert r._spec(_FakePlugin("mypkg", version="1.2.3")) == "mypkg==1.2.3"

    def test_no_version_returns_package(self) -> None:
        r = _make_reconciler()
        assert r._spec(_FakePlugin("mypkg")) == "mypkg"

    def test_url_package_ignores_version(self) -> None:
        r = _make_reconciler()
        url = "http://repo/pkg-1.0.whl"
        assert r._spec(_FakePlugin(url, version="1.0")) == url

    def test_whl_path_ignores_version(self) -> None:
        r = _make_reconciler()
        whl = "/dist/mypkg-1.0-py3-none-any.whl"
        assert r._spec(_FakePlugin(whl, version="1.0")) == whl

    def test_tar_gz_path_ignores_version(self) -> None:
        r = _make_reconciler()
        tgz = "/dist/mypkg-1.0.tar.gz"
        assert r._spec(_FakePlugin(tgz, version="1.0")) == tgz

    def test_urn_resolved_to_uri(self, monkeypatch) -> None:
        r = _make_reconciler()
        monkeypatch.setattr(r, "_resolve_urn", lambda urn: "http://repo/pkg-1.0.whl")
        assert (
            r._spec(_FakePlugin("urn:artifacts:abc", version="1.0"))
            == "http://repo/pkg-1.0.whl"
        )

    def test_urn_resolved_no_version(self, monkeypatch) -> None:
        r = _make_reconciler()
        monkeypatch.setattr(r, "_resolve_urn", lambda urn: "http://repo/pkg-1.0.whl")
        assert r._spec(_FakePlugin("urn:artifacts:abc")) == "http://repo/pkg-1.0.whl"


class TestShouldReinstall:
    def test_version_mismatch_needs_reinstall(self) -> None:
        plugin = _FakePlugin("mypkg", version="2.0.0")
        assert PluginReconciler._should_reinstall(plugin, "1.0.0") is True

    def test_version_match_no_reinstall(self) -> None:
        plugin = _FakePlugin("mypkg", version="2.0.0")
        assert PluginReconciler._should_reinstall(plugin, "2.0.0") is False

    def test_no_version_pin_no_reinstall(self) -> None:
        plugin = _FakePlugin("mypkg", version="")
        assert PluginReconciler._should_reinstall(plugin, "1.0.0") is False

    def test_url_package_always_reinstalls(self) -> None:
        plugin = _FakePlugin("http://repo/pkg-2.0.whl")
        assert PluginReconciler._should_reinstall(plugin, "1.0.0") is True

    def test_whl_path_always_reinstalls(self) -> None:
        plugin = _FakePlugin("/dist/mypkg-2.0-py3-none-any.whl")
        assert PluginReconciler._should_reinstall(plugin, "") is True

    def test_urn_package_always_reinstalls(self) -> None:
        plugin = _FakePlugin("urn:artifacts:abc-123")
        assert PluginReconciler._should_reinstall(plugin, "1.0.0") is True


class TestInstall:
    def test_spec_resolution_failure_returns_false(self, monkeypatch) -> None:
        """A failure to resolve the spec (e.g. URN not found) must not raise
        — it returns False so other pending plugins are still processed."""
        r = _make_reconciler()
        monkeypatch.setattr(
            r, "_spec", lambda plugin: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        assert r._install(_FakePlugin("urn:artifacts:abc")) is False
