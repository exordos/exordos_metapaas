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

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = []

    @property
    def migration_id(self):
        return "4d657461-0000-0000-0000-000000000010"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        expressions = [
            """\
CREATE TABLE metapaas_paas_types (
    uuid UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL DEFAULT '',
    description VARCHAR(255) NOT NULL DEFAULT '',
    project_id UUID NOT NULL,
    element_name VARCHAR(128) NOT NULL,
    package VARCHAR(512) NOT NULL,
    version VARCHAR(128) NOT NULL DEFAULT '',
    index_url VARCHAR(512) NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'NEW',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
""",
        ]
        for expression in expressions:
            session.execute(expression)

    def downgrade(self, session):
        session.execute("DROP TABLE IF EXISTS metapaas_paas_types;")


migration_step = MigrationStep()
