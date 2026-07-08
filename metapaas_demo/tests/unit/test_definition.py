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

from metapaas_demo.definition import DemoDefinition


class TestDemoDefinition:
    """Conformance of the demo PaaSDefinition to the metapaas plugin contract."""

    def test_slug(self) -> None:
        assert DemoDefinition.slug == "demo"

    def test_element_name(self) -> None:
        assert DemoDefinition.element_name == "metapaas-demo"

    def test_migrations_path_set(self) -> None:
        assert DemoDefinition().get_migrations_path() is not None

    def test_agent_models_has_instances_and_versions(self) -> None:
        models = DemoDefinition().get_agent_models()
        assert "versions" in models
        assert "instances" in models

    def test_agent_filters_has_instances_and_versions(self) -> None:
        filters = DemoDefinition().get_agent_filters()
        assert "versions" in filters
        assert "instances" in filters
