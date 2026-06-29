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

import metapaas_demo.dataplane.driver as drv_module


class _Stub:
    def __init__(self, name):
        self.name = name

    _build_env = drv_module.DemoInstance._build_env


class TestBuildEnv:
    def test_contains_name(self) -> None:
        inst = _Stub("my-demo")
        env = inst._build_env()
        assert "DEMO_NAME=my-demo" in env

    def test_contains_comment(self) -> None:
        inst = _Stub("x")
        env = inst._build_env()
        assert env.startswith("# Demo node environment")

    def test_ends_with_newline(self) -> None:
        inst = _Stub("x")
        assert inst._build_env().endswith("\n")


class TestWriteFileAtomic:
    def test_returns_true_when_changed(self, tmp_path) -> None:
        p = tmp_path / "demo.env"
        assert drv_module._write_file_atomic(str(p), "new\n") is True

    def test_returns_false_when_unchanged(self, tmp_path) -> None:
        p = tmp_path / "demo.env"
        p.write_text("same\n")
        assert drv_module._write_file_atomic(str(p), "same\n") is False

    def test_overwrites_different_content(self, tmp_path) -> None:
        p = tmp_path / "demo.env"
        p.write_text("old\n")
        drv_module._write_file_atomic(str(p), "new\n")
        assert p.read_text() == "new\n"
