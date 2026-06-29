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

import enum

from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.dm import models, properties, relationships, types
from restalchemy.storage.sql import orm


class DemoStatus(str, enum.Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"


class DemoVersion(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    ua_models.TargetResourceMixin,
):
    __tablename__ = "demo_versions"

    image = properties.property(types.String(max_length=2048))


class DemoInstance(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithProject,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "demo_instances"

    name = properties.property(types.String(min_length=1, max_length=255))
    status = properties.property(
        types.Enum([s.value for s in DemoStatus]),
        default=DemoStatus.NEW.value,
    )
    ipsv4 = properties.property(
        types.TypedList(types.String(max_length=15)),
        default=lambda: [],
    )
    cpu = properties.property(types.Integer(min_value=1, max_value=128))
    ram = properties.property(types.Integer(min_value=512, max_value=1024**3))
    disk_size = properties.property(types.Integer(min_value=8, max_value=1024**3))
    version = relationships.relationship(DemoVersion, required=True, read_only=True)

    def _validate_update(self, session=None):
        disk_size = self.properties["disk_size"]
        if disk_size.is_dirty() and disk_size.old_value > self.disk_size:
            raise ValueError("disk_size shrink is not supported yet")

    def update(self, session=None, force=False):
        self._validate_update(session=session)
        super().update(session=session, force=force)
