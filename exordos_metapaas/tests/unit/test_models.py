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
