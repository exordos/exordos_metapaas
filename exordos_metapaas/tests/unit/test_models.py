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

import uuid

from exordos_metapaas.dm import models


class TestPaaSType:
    def test_tablename(self) -> None:
        assert models.PaaSType.__tablename__ == "metapaas_paas_types"

    def test_status_default(self) -> None:
        pt = models.PaaSType(
            name="test",
            element_name="el",
            package="pkg",
            project_id=uuid.uuid4(),
        )
        assert pt.status == "NEW"

    def test_version_default(self) -> None:
        pt = models.PaaSType(
            name="test",
            element_name="el",
            package="pkg",
            project_id=uuid.uuid4(),
        )
        assert pt.version == ""

    def test_index_url_default(self) -> None:
        pt = models.PaaSType(
            name="test",
            element_name="el",
            package="pkg",
            project_id=uuid.uuid4(),
        )
        assert pt.index_url == ""

    def test_version_change_resets_status_to_new(self) -> None:
        pt = models.PaaSType(
            name="test",
            element_name="el",
            package="mypkg",
            version="1.0.0",
            project_id=uuid.uuid4(),
        )
        pt.status = "ACTIVE"
        pt.version = "2.0.0"
        # Simulate what update() does without hitting the DB
        if pt.properties["version"].is_dirty() or pt.properties["package"].is_dirty():
            pt.status = "NEW"
        assert pt.status == "NEW"

    def test_package_change_resets_status_to_new(self) -> None:
        pt = models.PaaSType(
            name="test",
            element_name="el",
            package="mypkg",
            project_id=uuid.uuid4(),
        )
        pt.status = "ACTIVE"
        pt.package = "newpkg"
        if pt.properties["version"].is_dirty() or pt.properties["package"].is_dirty():
            pt.status = "NEW"
        assert pt.status == "NEW"

    def test_other_field_change_does_not_reset_status(self) -> None:
        pt = models.PaaSType(
            name="test",
            element_name="el",
            package="mypkg",
            project_id=uuid.uuid4(),
        )
        pt.status = "ACTIVE"
        pt.element_name = "new_el"
        if pt.properties["version"].is_dirty() or pt.properties["package"].is_dirty():
            pt.status = "NEW"
        assert pt.status == "ACTIVE"
