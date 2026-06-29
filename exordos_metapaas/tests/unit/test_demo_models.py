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

from metapaas_demo.controlplane.dm import models


class TestDemoVersion:
    def test_tablename(self) -> None:
        assert models.DemoVersion.__tablename__ == "demo_versions"


class TestDemoInstance:
    def test_tablename(self) -> None:
        assert models.DemoInstance.__tablename__ == "demo_instances"

    def test_status_values(self) -> None:
        values = [s.value for s in models.DemoStatus]
        assert "NEW" in values
        assert "IN_PROGRESS" in values
        assert "ACTIVE" in values
        assert "ERROR" in values
