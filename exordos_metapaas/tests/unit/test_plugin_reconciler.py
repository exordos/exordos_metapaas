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
from exordos_metapaas.services.plugin_reconciler import _looks_like_url


class _FakePlugin:
    def __init__(self, package, version="", index_url=""):
        self.package = package
        self.version = version
        self.index_url = index_url


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
        assert (
            PluginReconciler._spec(_FakePlugin("mypkg", version="1.2.3"))
            == "mypkg==1.2.3"
        )

    def test_no_version_returns_package(self) -> None:
        assert PluginReconciler._spec(_FakePlugin("mypkg")) == "mypkg"

    def test_url_package_ignores_version(self) -> None:
        url = "http://repo/pkg-1.0.whl"
        assert PluginReconciler._spec(_FakePlugin(url, version="1.0")) == url

    def test_whl_path_ignores_version(self) -> None:
        whl = "/dist/mypkg-1.0-py3-none-any.whl"
        assert PluginReconciler._spec(_FakePlugin(whl, version="1.0")) == whl

    def test_tar_gz_path_ignores_version(self) -> None:
        tgz = "/dist/mypkg-1.0.tar.gz"
        assert PluginReconciler._spec(_FakePlugin(tgz, version="1.0")) == tgz


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
