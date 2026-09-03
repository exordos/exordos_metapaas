# How to Build a New PaaS Plugin for MetaPaaS

This guide walks through building a new PaaS service (e.g., mail server, database, etc.) using the MetaPaaS framework.

**Scope:** A PaaS plugin is a stateful service with:
- **CP (Control Plane):** REST API for provisioning/lifecycle (Python package → pip install on metapaas-cp)
- **DP (Data Plane):** VM image running the actual service (built via `exordos build` with `dp_install.sh` + `dp_bootstrap.sh`, Zstandard-compressed)
- **Integration:** Manifest template + PluginReconciler for auto-deployment

---

## Architecture Overview

```
┌─ exordos_core (user-facing, manages PaaS instances)
│
├─ exordos_metapaas (runtime)
│  ├─ CP image (metapaas-cp VM node)
│  ├─ Plugin reconciler (watches metapaas_paas_types, pip-installs plugins on CP)
│  └─ Base schema (PaaSType, PaaSVersion, status, nodes)
│
└─ exordos_<name>/ (your plugin repo, one Python package)
   ├─ exordos/
   │  ├─ exordos.yaml          (build config: DP image + manifest)
   │  ├─ images/
   │  │  ├─ dp_install.sh      (runs during DP image build)
   │  │  └─ dp_bootstrap.sh    (runs on first DP node boot)
   │  └─ manifests/
   │     ├─ <name>aas.yaml.j2  (element manifest: IAM, plugin type, DP versions)
   │     └─ example_<name>.yaml.j2  (declarative example instance for testing)
   ├─ etc/
   │  ├─ exordos_metapaas/     (agent .conf + logging.yaml, bundled into DP image)
   │  └─ systemd/              (exordos-metapaas-<name>-agent.service, -configure.service)
   ├─ exordos_<name>/          (Python package — CP + DP code, one package)
   │  ├─ definition.py         (PaaSDefinition: slug, element_name, builders, migrations)
   │  ├─ constants.py
   │  ├─ utils.py
   │  ├─ controlplane/
   │  │  ├─ dm/models.py       (user-facing CP models: Instance, Version, child resources)
   │  │  ├─ api/
   │  │  │  ├─ controllers.py  (REST controllers with IAM)
   │  │  │  └─ routes.py
   │  │  ├─ infra/
   │  │  │  ├─ dm/models.py    (infra-layer models: NodeSet, Config wrappers)
   │  │  │  └─ services/builder.py  (CoreInfraBuilder: nodes, keys, config regen)
   │  │  └─ paas/
   │  │     ├─ dm/models.py    (paas-layer models: Instance view for builder)
   │  │     └─ services/builder.py  (PaaSBuilder: payload assembly, DP facts sync)
   │  ├─ dataplane/
   │  │  └─ driver.py          (MetaDataPlaneModel + CapabilityDriver; runs on DP node)
   │  ├─ migrations/           (restalchemy migrations, tablename prefix = slug)
   │  └─ tests/
   │     ├─ unit/              (driver logic, model behaviour — no live stand)
   │     └─ functional/        (E2E against live stand; conftest.py + prepare_env.py)
   ├─ pyproject.toml           (entrypoints: exordos_metapaas_paas + gcl_sdk_universal_agent)
   ├─ tox.ini, Makefile
   └─ .github/workflows/       (tests.yaml + func_tests.yaml)
```

---

## Step 1: Create the Repository Structure

```bash
cd ~/exo
mkdir exordos_<name>
cd exordos_<name>

mkdir -p exordos/{manifests,images}
mkdir -p etc/{exordos_metapaas,systemd}
mkdir -p exordos_<name>/{controlplane/{dm,api,infra/{dm,services},paas/{dm,services}},dataplane,migrations,tests/{unit,functional}}
mkdir -p .github/workflows
```

One Python package `exordos_<name>` contains both CP and DP code. DP build scripts live under `exordos/images/`. Agent config and systemd units live under `etc/`.

---

## Step 2: Define the Data Plane (DP) Image

The DP image is built by the `exordos build` toolchain using two shell scripts:
- `dp_install.sh` — runs during image build (apt packages, binaries, systemd units, Python venv)
- `dp_bootstrap.sh` — runs on first boot of each DP node (persistent disk setup, service start)

Both live under `exordos/images/` and are referenced in `exordos/exordos.yaml`.

**File:** `exordos/images/dp_install.sh`

Key requirements:
- Start with `set -eu -o pipefail`
- apt-install service software
- Copy bootstrap script to `/var/lib/exordos/bootstrap/scripts/` and `chmod +x`
- Build the `exordos_metapaas` wheel locally (it is **not on PyPI** — see Pitfall #8), then `uv sync --find-links`
- Install and link the universal agent binary (it loads your CapabilityDriver via entry-point)
- Install systemd unit files; enable the agent unit

Example skeleton:
```bash
#!/usr/bin/env bash
set -eu -o pipefail
set -x

PLUGIN_PATH="/opt/exordos_metapaas"
RUNTIME_PATH="/opt/exordos_metapaas_runtime"
VENV_PATH="$PLUGIN_PATH/.venv"
BOOTSTRAP_PATH="/var/lib/exordos/bootstrap/scripts"
SYSTEMD_SERVICE_DIR=/etc/systemd/system/

sudo apt update && sudo apt dist-upgrade -y
sudo apt install -y <service-packages> libev-dev

sudo cp "$PLUGIN_PATH/exordos/images/dp_bootstrap.sh" \
    "$BOOTSTRAP_PATH/0100-<name>-dp-bootstrap.sh"
sudo chmod +x "$BOOTSTRAP_PATH/0100-<name>-dp-bootstrap.sh"

cd "$PLUGIN_PATH"
# exordos_metapaas is not on PyPI — build a wheel from the local copy first
uv build --wheel --out-dir /tmp/<name>-wheels/ "$RUNTIME_PATH"
uv sync --find-links /tmp/<name>-wheels/

sudo ln -sf "$VENV_PATH/bin/exordos-universal-agent" /usr/bin/exordos-universal-agent

sudo cp "$PLUGIN_PATH/etc/systemd/exordos-metapaas-<name>-agent.service" "$SYSTEMD_SERVICE_DIR/"
sudo cp "$PLUGIN_PATH/etc/systemd/exordos-metapaas-<name>-configure.service" "$SYSTEMD_SERVICE_DIR/"
sudo systemctl enable exordos-metapaas-<name>-agent
```

**File:** `exordos/images/dp_bootstrap.sh`

Runs once on first boot. Mounts persistent disk, migrates log dirs, starts configure service:

```bash
#!/usr/bin/env bash
set -eu -o pipefail
set -x

source /usr/local/lib/exordos/lib_bootstrap.sh

PERSISTENT_DISK=$(find_persistent_disk)
prepare_persistent_disk "$PERSISTENT_DISK" "$PERSISTENT_MOUNT" "xfs"

if [[ -n "$PERSISTENT_DISK" ]]; then
    migrate_to_persistent "/var/log" "${PERSISTENT_MOUNT}/var/log"
    persist_migrate_complete
fi

# Configure service starts once CP delivers /etc/exordos_metapaas/<name>.env
sudo systemctl enable --now exordos-metapaas-<name>-configure

echo "Bootstrap completed successfully."
```

---

## Step 3: Define the Control Plane (CP) Code

The CP code runs on the metapaas-cp VM and provides the REST API.

### 3.1 Models

**File:** `exordos_<name>/controlplane/dm/models.py`

Models use `restalchemy.dm` primitives. A service typically needs at minimum a **Version** model (the DP image catalog) and an **Instance** model (what users create). Nested resources (e.g., mail accounts, S3 buckets) are additional models that reference the instance.

```python
import enum

from restalchemy.dm import models, properties, relationships, types
from restalchemy.storage.sql import orm
from gcl_sdk.agents.universal.dm import models as ua_models


class FooStatus(str, enum.Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"


class FooVersion(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    ua_models.TargetResourceMixin,  # needed so the runtime can track this as a resource
):
    __tablename__ = "foo_versions"

    image = properties.property(types.String(max_length=2048))


class FooInstance(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithProject,  # gives project_id → multitenancy
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "foo_instances"

    status = properties.property(
        types.Enum([s.value for s in FooStatus]),
        default=FooStatus.NEW.value,
    )
    ipsv4 = properties.property(
        types.TypedList(types.String(max_length=15)), default=lambda: []
    )
    cpu = properties.property(types.Integer(min_value=1, max_value=128))
    ram = properties.property(types.Integer(min_value=512, max_value=1024**3))
    disk_size = properties.property(types.Integer(min_value=8, max_value=1024**3))
    version = relationships.relationship(FooVersion, required=True, read_only=True)
```

Key points:
- `ModelWithProject` provides `project_id` — this is what makes instances **per-user** (multitenancy at data layer)
- `TargetResourceMixin` on Version lets the metapaas runtime track it as a deployable resource
- `status` and `ipsv4` are read-only from the user's perspective (set by the orchestrator)
- **Do not add passwords/secrets to the Instance model** — credentials belong in child resources (accounts, access keys) or are generated on the DP side

**Child resources and `_touch_parent`:** child models (accounts, buckets, etc.) that change the DP payload must bump the parent instance's `updated_at` on every mutation so gservice picks up the change:

```python
class FooAccount(...):
    instance = relationships.relationship(FooInstance, required=True, read_only=True)
    password_hash = properties.property(types.String(...), required=True)

    def _touch_parent(self, session=None):
        self.instance.update(force=True)

    def insert(self, session=None):
        self._maybe_hash_password()
        super().insert(session=session)
        self._touch_parent(session=session)

    def update(self, session=None, force=False):
        self._maybe_hash_password()
        super().update(session=session, force=force)
        self._touch_parent(session=session)

    def delete(self, session=None, **kwargs):
        res = super().delete(session=session, **kwargs)
        self._touch_parent(session=session)
        return res
```

**Salted hash fields (`_maybe_hash_password` pattern):** the core-agent stores the original plaintext from the create request in the orch target and re-applies it on every cycle. A naive `sha512_crypt.hash(plaintext)` generates a **new random salt each time** → DB hash changes every ~3s → DP driver sees a diff → service reloads in a loop. Fix: keep the stored hash when plaintext still matches it:

```python
def _maybe_hash_password(self):
    if self.password_hash and not _is_crypt_hash(self.password_hash):
        old_hash = self.properties[
            "password_hash"
        ].old_value  # value before this setattr
        if (
            old_hash
            and _is_crypt_hash(old_hash)
            and sha512_crypt.verify(self.password_hash, old_hash)
        ):
            self.password_hash = old_hash  # same password, keep existing hash
        else:
            self.password_hash = sha512_crypt.hash(self.password_hash)
```

### 3.1b PaaS-layer model & PaaS→IaaS readiness gating

**File:** `exordos_<name>/controlplane/paas/dm/models.py`

The PaaS-layer `Instance` model is a *view* of the CP `Instance` model that the
`PaaSBuilder` operates on. It mixes in `InstanceWithDerivativesMixin` (so the
builder can schedule derivative resources to dataplane agents) and, critically,
**`DependenciesActiveReadinessMixin`** to gate reconciliation on the IaaS
instance being `ACTIVE`:

```python
import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra.dm import models as sdk_models
from restalchemy.dm import filters as ra_filters

from exordos_foo.controlplane.dm import models
from exordos_foo.controlplane.paas.dm import models as paas_models


class FooInstanceNode(
    paas_models.ModelWithUUID,
    ua_models.TargetResourceKindAwareMixin,
):
    name = ...  # carried to the dataplane

    @classmethod
    def get_resource_kind(cls) -> str:
        return "foo_instance_node"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        return frozenset(("uuid", "name"))


class FooInstance(
    models.FooInstance,  # the CP model
    ua_models.InstanceWithDerivativesMixin,
    ua_models.DependenciesActiveReadinessMixin,  # ← gate on IaaS ACTIVE
):
    __master_model__ = sdk_models.NodeSet
    __derivative_model_map__ = {"foo_instance_node": FooInstanceNode}

    @classmethod
    def get_resource_kind(cls) -> str:
        return "foo_instance"

    def get_readiness_dependencies(self) -> tp.Collection[ua_models.RI]:
        """Depend on the IaaS instance being ACTIVE.

        The PaaS derivative (foo_instance_node) is scheduled to the dataplane
        agent, which only registers after the VM boots. The IaaS instance
        becomes ACTIVE once the compute set is provisioned — a reliable signal
        that the agent is (or is about to be) registered. Without this gate the
        builder tries to persist the derivative before the agent row exists in
        ua_agents, causing a foreign-key violation that rolls back the entire
        transaction (including any child-resource content fetched in the same
        iteration).
        """
        return (ua_models.RI("foo_instance_iaas", self.uuid),)

    def get_resource_target_fields(self) -> tp.Collection[str]:
        return frozenset(("uuid", "name"))

    def get_actual_nodeset(self):
        res = ua_models.Resource.objects.get_one(
            filters={
                "uuid": ra_filters.EQ(self.uuid),
                "kind": ra_filters.EQ("node_set"),
            }
        )
        return self.__master_model__.from_ua_resource(res)
```

**Why this matters:** without `DependenciesActiveReadinessMixin`, the PaaS
builder can persist `ua_target_resources` (the derivative) before the dataplane
agent has registered in `ua_agents`. That triggers a foreign-key violation on
`ua_target_resources.agent_uuid → ua_agents.uuid`, which rolls back the whole
reconciliation transaction — including any child-resource work (e.g. dashboard
content fetched from an external provider) done in the same iteration. The
instance then appears stuck in `IN_PROGRESS` and never converges. This is a
**race**, not a deterministic failure: it depends on whether the IaaS instance
reaches `ACTIVE` before the builder's first tick, so it can pass in dev and
fail on a slower stand. Adding the mixin makes the builder skip the instance
until the IaaS dependency is `ACTIVE`, which is the correct ordering.

The `RI` (resource identifier) kind `"foo_instance_iaas"` must match the
resource kind the `CoreInfraBuilder` produces for the IaaS instance — i.e. the
`get_resource_kind()` of the infra-layer instance model (see
`controlplane/infra/dm/models.py`). See `exordos_observability`'s
`victoria/controlplane/paas/dm/models.py` and `grafana/controlplane/paas/dm/models.py`
for the verified reference implementation.

### 3.2 Controllers

**File:** `exordos_<name>/controlplane/api/controllers.py`

Controllers wire together IAM enforcement and REST routing. Use `gcl_iam.controllers` mixins — they implement the `__policy_service_name__.<resource>.<action>` check against Core IAM automatically.

```python
from gcl_iam import controllers as iam_controllers
from restalchemy.api import constants
from restalchemy.api import controllers as ra_controllers
from restalchemy.api import field_permissions as field_p
from restalchemy.api import resources as ra_resources

from exordos_foo import models


class FooController(ra_controllers.RoutesListController):
    """Entry point: /v1/types/foo/"""

    __TARGET_PATH__ = "/v1/types/foo/"


class FooVersionController(
    iam_controllers.PolicyBasedWithoutProjectController,  # no project scope — shared catalog
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = (
        "exordos_foo"  # ← must match permission name prefix in manifest
    )
    __policy_name__ = "foo_version"  # ← exordos_foo.foo_version.read

    __resource__ = ra_resources.ResourceByRAModel(
        model_class=models.FooVersion,
        convert_underscore=False,
        process_filters=True,
    )


class FooInstanceController(
    iam_controllers.PolicyBasedController,  # project-scoped; checks project_id
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "exordos_foo"  # ← exordos_foo.foo_instance.*
    __policy_name__ = "foo_instance"

    __resource__ = ra_resources.ResourceByRAModel(
        model_class=models.FooInstance,
        convert_underscore=False,
        process_filters=True,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {constants.ALL: field_p.Permissions.RO},
                "ipsv4": {constants.ALL: field_p.Permissions.RO},
            },
        ),
    )
```

How permissions map to manifest:
- `__policy_service_name__ = "exordos_foo"` + `__policy_name__ = "foo_instance"` → framework checks `exordos_foo.foo_instance.<action>` in Core IAM
- That permission must exist in Core and be bound to the user's role — this happens in the **manifest** (Step 4)

### 3.3 PaaS Definition

**File:** `exordos_<name>/definition.py` (at package root, not inside controlplane)

This is the plugin's entry point class. The metapaas runtime loads it via the `exordos_metapaas_paas` entrypoint group:

```python
import os

from exordos_metapaas.registry import PaaSDefinition
from exordos_foo import routes


class FooDefinition(PaaSDefinition):
    slug = "foo"
    element_name = "foo-aas"  # ← MUST match element registration name in manifest

    def get_type_route(self):
        return routes.FooRoute

    def get_migrations_path(self):
        return os.path.join(os.path.dirname(__file__), "migrations")

    def get_builders(self, core_username, core_password, core_api_base_url, project_id):
        from exordos_foo.infra_builder import CoreInfraBuilder
        from exordos_foo.infra_models import FooInstance as InfraFooInstance
        from exordos_foo.paas_builder import FooInstanceBuilder
        from exordos_foo.paas_models import FooInstance as PaaSFooInstance

        return [
            CoreInfraBuilder(
                core_username=core_username,
                core_password=core_password,
                core_api_base_url=core_api_base_url,
                project_id=project_id,
                instance_model=InfraFooInstance,
            ),
            FooInstanceBuilder(instance_model=PaaSFooInstance),
        ]

    def get_agent_models(self):
        return {
            "versions": "exordos_foo.models:FooVersion",
            "instances": "exordos_foo.infra_models:FooInstance",
        }

    def get_agent_filters(self):
        return {
            "versions": "description",  # versions filtered by project id in description
            "instances": "project_id",
        }
```

> **Critical:** `element_name` must match exactly the element's registered name (what you pass to `exordos em elements install`). The framework derives the version namespace as `$<element_name>.types.<slug>.versions`. If `element_name = "foo"` but the element is registered as `"foo-aas"`, the namespace won't be found and the DP version catalog will be empty.

### 3.4 DP Driver

**File:** `exordos_<name>/dataplane/driver.py` (ships inside the DP image; loaded by the universal agent via entrypoint)

The driver implements `MetaDataPlaneModel` and runs on the DP node. The agent calls it every few seconds — **it must be idempotent**.

Three required methods:

```python
from gcl_sdk.agents.universal.drivers import meta


class FooInstance(meta.MetaDataPlaneModel):
    # ... property declarations ...

    def dump_to_dp(self) -> None:
        """Apply target state to system files/services.

        Use _write_file_atomic — only write and reload if content differs.
        Never call systemctl reload/restart unconditionally.
        """
        changed = _write_file_atomic(CONFIG_PATH, self._build_config())
        if changed:
            subprocess.run(["systemctl", "reload", "myservice"], check=True)

    def restore_from_dp(self) -> None:
        """Read ACTUAL on-disk state into self.

        Must reflect reality — not empty defaults. If this returns {} for a
        field that has data on disk, actual≠target every cycle → dump_to_dp()
        fires every cycle → service restarts in a loop.
        """
        self.accounts = self._read_accounts_from_disk()  # real disk read

    def delete_from_dp(self) -> None:
        pass  # clean up resources on instance delete

    def update_on_dp(self) -> None:
        self.dump_to_dp()  # standard delegation


def _write_file_atomic(path: str, content: str) -> bool:
    """Write file only if content changed; return True if it was written."""
    try:
        with open(path) as f:
            if f.read() == content:
                return False
    except FileNotFoundError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.rename(tmp, path)
    return True
```

### 3.5 Plugin Entrypoints in pyproject.toml

```toml
# The metapaas runtime discovers your PaaSDefinition via this group
[project.entry-points."exordos_metapaas_paas"]
foo = "exordos_foo.definition:FooDefinition"

# The core agent discovers your CapabilityDriver via this group
[project.entry-points."gcl_sdk_universal_agent"]
FooCapabilityDriver = "exordos_foo.driver:FooCapabilityDriver"
```

When the `foo-aas` element is installed in exordos_core, the runtime:
1. Resolves the `package` field (a wheel artifact URN built by `exordos build`) and `pip install`s it on metapaas-cp
2. Loads `FooDefinition` via entrypoint
3. Calls `get_type_route()` → mounts routes at `/v1/types/foo/`
4. Calls `get_migrations_path()` → applies pending DB migrations
5. Calls `get_builders(...)` → instantiates infra + paas builders

IAM permissions are declared entirely in the manifest (Step 4) — no Python-side permission list needed.

---

## Step 4: Build Manifest Template

**File:** `exordos/manifests/<name>-aas.yaml.j2`

This is a Jinja2 template rendered by `exordos build`. The rendered YAML is what exordos_core installs — it registers IAM permissions, binds them to the owner role, and registers the plugin type with MetaPaaS.

### IAM Model

Permissions are declared in the manifest, **not in Python code**. The relationship is:

```
Controller:  __policy_service_name__ = "exordos_foo"
             __policy_name__ = "foo_instance"
           ──► checks permission name:  "exordos_foo.foo_instance.<action>"

Manifest:   $core.iam.permissions:
              foo_instance_create:
                name: "exordos_foo.foo_instance.create"   ← same name

            $core.iam.permissionbinding:
              foo_instance_create_binding:
                role: "726f6c65-0000-0000-0000-000000000002"   ← owner role UUID
                permission: $core.iam.permissions.$foo_instance_create:uuid
                project_id: "4d657461-0000-0000-0000-000000000002"  ← metapaas project
```

The **owner role** (`726f6c65-0000-0000-0000-000000000002`) is a well-known UUID in exordos_core. The **metapaas project** (`4d657461-0000-0000-0000-000000000002`) is where all metapaas resources live.

### Full Manifest Template

```yaml
name: "foo-aas"
description: "Install the foo PaaS into the metapaas control plane."
schema_version: 1
version: "{{ version }}"
api_version: "v1"

requirements:
  core:
    from_version: "0.0.0"
  metapaas:
    from_version: "0.0.0"

resources:

  # Register plugin with MetaPaaS. PluginReconciler watches this and
  # pip-installs the package at runtime on metapaas-cp. The package is
  # resolved from the wheel artifact built by the common build (see the
  # `artifacts:` block in exordos.yaml) — no manual index_url or repository
  # manifest-var needed.
  $metapaas.types:
    foo:
      name: "foo"
      element_name: "foo-aas"      # ← must match the element registration name
      package: "{{ artifacts.pypi_package }}"
      project_id: "4d657461-0000-0000-0000-000000000002"

  # Permission names must match __policy_service_name__.__policy_name__.<action>
  $core.iam.permissions:
    foo_instance_create:
      name: "exordos_foo.foo_instance.create"
    foo_instance_read:
      name: "exordos_foo.foo_instance.read"
    foo_instance_update:
      name: "exordos_foo.foo_instance.update"
    foo_instance_delete:
      name: "exordos_foo.foo_instance.delete"
    foo_version_read:
      name: "exordos_foo.foo_version.read"

  $core.iam.permissionbinding:
    foo_instance_create_binding:
      role: "726f6c65-0000-0000-0000-000000000002"
      permission: $core.iam.permissions.$foo_instance_create:uuid
      project_id: "4d657461-0000-0000-0000-000000000002"
    foo_instance_read_binding:
      role: "726f6c65-0000-0000-0000-000000000002"
      permission: $core.iam.permissions.$foo_instance_read:uuid
      project_id: "4d657461-0000-0000-0000-000000000002"
    foo_instance_update_binding:
      role: "726f6c65-0000-0000-0000-000000000002"
      permission: $core.iam.permissions.$foo_instance_update:uuid
      project_id: "4d657461-0000-0000-0000-000000000002"
    foo_instance_delete_binding:
      role: "726f6c65-0000-0000-0000-000000000002"
      permission: $core.iam.permissions.$foo_instance_delete:uuid
      project_id: "4d657461-0000-0000-0000-000000000002"
    foo_version_read_binding:
      role: "726f6c65-0000-0000-0000-000000000002"
      permission: $core.iam.permissions.$foo_version_read:uuid
      project_id: "4d657461-0000-0000-0000-000000000002"

  # Dataplane version catalog. `description` carries the EM project id
  # (exordos_core EM_PROJECT_ID, must match the core-agent [filters] value
  # rendered by exordos-metapaas-render-config) for the version filter.
  # Namespace: $<element_name>.types.<slug>.versions  ← note: element_name, not slug
  #
  # The image is referenced via the `images.<name>` URN variable, which the
  # `exordos build` toolchain resolves to the built DP image artifact. The
  # `<name>` matches the `name:` field of the image entry in exordos.yaml
  # (dashes → underscores). No `repository` manifest-var or hardcoded URL.
  $foo-aas.types.foo.versions:
    foo_v1:
      name: "foo_v1"
      description: "12345678-c625-4fee-81d5-f691897b8142"
      image: "{{ images.exordos_metapaas_foo_dp }}"

exports:
  foo_v1:
    link: "$foo-aas.types.foo.versions.$foo_v1"
```

---

## Step 5: Build Configuration

**File:** `exordos/exordos.yaml`

Two deps are always needed: the plugin package itself and `exordos_metapaas` runtime (which is not on PyPI and must be built from source in `dp_install.sh`).

The PyPI wheel is built as part of the common `exordos build` run via an `artifacts:` block on the image element entry. The built wheel is exposed to the manifest as the `{{ artifacts.pypi_package }}` variable (referenced in `$metapaas.types` `package:`) and to the DP image build as a findable artifact. This replaces the old `--manifest-var index_url=...` / `--manifest-var repository=...` Makefile flags — the build toolchain resolves everything via URNs.

```yaml
build:

  deps:
    # Plugin package: copied into the image so dp_install.sh can run uv sync
    - dst: /opt/exordos_metapaas
      path:
        src: ../../exordos_<name>
      exclude:
        - .venv
        - .tox
        - output

    # exordos_metapaas runtime: not on PyPI, wheel is built locally during dp_install.sh
    - dst: /opt/exordos_metapaas_runtime
      path:
        src: ../../exordos_metapaas
      exclude:
        - .venv
        - .tox
        - output

    # Optional: local gcl_sdk override for development
    - dst: /opt/gcl_sdk
      optional: true
      path:
        env: LOCAL_GENESIS_SDK_PATH

  elements:
    # Manifest-only entry: registers IAM + plugin type (no image built)
    - manifest: manifests/foo-aas.yaml.j2

    # Image entry: same manifest + DP image build + PyPI wheel artifact
    - manifest: manifests/foo-aas.yaml.j2
      artifacts:
        # Build the plugin wheel and expose it as `{{ artifacts.pypi_package }}`
        # in the manifest (used by $metapaas.types.<slug>.package). The glob
        # is relative to exordos.yaml's directory (../dist/ = repo root dist/).
        - script: images/build_wheel.sh
          name: pypi_package
          artifacts:
            - ../dist/*.whl
      images:
        - name: exordos-metapaas-foo-dp
          format: GEN_IMG_FORMAT_CORE=zst
          profile: exordos_base
          script: images/dp_install.sh
          override:
            disk_size: "6G"
            use_backing_file: true
```

**File:** `exordos/images/build_wheel.sh` — a self-contained script that builds the plugin wheel into `dist/` using a local venv (prefers `uv`, falls back to `python3 -m venv`). It `cd`s to the repo root (`$SCRIPT_DIR/../..`, where `pyproject.toml` lives) and runs `python -m build --wheel`. Copy it verbatim from `exordos_observability/exordos/images/build_wheel.sh` or `metapaas_demo/exordos/images/build_wheel.sh`.

The DP image `name:` (`exordos-metapaas-foo-dp`) is what the manifest's `image: "{{ images.exordos_metapaas_foo_dp }}"` resolves to — dashes in the image name become underscores in the `images.*` variable.

---

## Step 6: Testing Structure

### 6.1 Unit Tests

**File:** `exordos_<name>/tests/unit/test_driver.py`  (under `exordos_<name>/tests/`)

Test driver logic and model behaviour in isolation (no live stand needed):

```python
import pytest
from unittest.mock import patch, MagicMock
from exordos_foo.driver import FooCapabilityDriver


def test_driver_creates_config_from_accounts():
    driver = FooCapabilityDriver()
    # ... test specific driver logic
```

### 6.2 Functional Tests

**File:** `exordos_<name>/tests/functional/test_<name>_provision.py`

Test end-to-end against a live stand using the real CP/DP path. Follow the pattern established in `exordos_mail` and `exordos_s3`:

- Use `conftest.py` for instance lifecycle fixtures (create on module start, delete on teardown)
- Use `prepare_env.py` for CI bootstrap (build + install + wait for ACTIVE)
- Use polling helpers to wait for CP→DP propagation (don't sleep blindly)
- Never send real traffic outside the test environment

```python
import pytest


def test_service_behaves_after_account_create(api_client, instance_uuid, dp_host):
    """Create a resource via CP API, wait for DP sync, verify on DP."""
    acc = api_client.create(...)
    _wait_for_condition(dp_host, ...)
    # assert DP reflects the change
```

---

## Step 7: Standard Tooling (Makefile, tox.ini, pyproject.toml)

Copy from `exordos_mail` or `exordos_s3` and adapt package names.

**File:** `pyproject.toml`
```toml
[project]
name = "exordos_foo"
description = "Foo PaaS plugin for the Exordos MetaPaaS runtime"
dynamic = ["version"]
requires-python = ">=3.10"
dependencies = [
    "oslo.config>=3.22.2,<10.0.0",
    "restalchemy>=15.0.0,<16.0.0",
    "gcl_iam>=1.0.3,<2.0.0",
    "gcl_looper>=1.2.3,<2.0.0",
    "gcl_sdk>=3.0.0,<4.0.0",
    "exordos_metapaas>=0.0.5",
]

[project.entry-points."exordos_metapaas_paas"]
foo = "exordos_foo.definition:FooDefinition"

[project.entry-points."gcl_sdk_universal_agent"]
FooCapabilityDriver = "exordos_foo.driver:FooCapabilityDriver"

[build-system]
requires = ["setuptools>=75.3.3", "setuptools_scm>=8"]
build-backend = "setuptools.build_meta"

[tool.setuptools_scm]

[project.optional-dependencies]
test = [
    "coverage>=4.0",
    "pytest>=8.0.0,<9.0.0",
    "exordos>=2.0.0,<3.0.0",
]
ruff = ["ruff"]
mypy = ["mypy"]

[tool.setuptools.package-data]
exordos_foo = ["migrations/*.py"]
```

**File:** `tox.ini`
```ini
[tox]
envlist = py312, ruff-check, mypy, py312-functional
isolated_build = True

[testenv]
package = wheel
extras = test
commands = pytest exordos_foo/tests/unit {posargs}

[testenv:py{310,311,312,313,314}-functional]
extras = test
setenv =
    EXORDOS_ENDPOINT = {env:EXORDOS_ENDPOINT:http://10.20.0.2:11010}
    METAPAAS_USERNAME = {env:METAPAAS_USERNAME:metapaas}
    METAPAAS_PASSWORD = {env:METAPAAS_PASSWORD:}
    EXORDOS_USERNAME = {env:EXORDOS_USERNAME:admin}
    EXORDOS_PASSWORD = {env:EXORDOS_PASSWORD:}
commands = pytest exordos_foo/tests/functional {posargs}

[testenv:ruff-check]
extras = ruff
commands = ruff check exordos_foo

[testenv:ruff]
extras = ruff
commands = ruff format exordos_foo

[testenv:mypy]
extras = mypy
commands = mypy exordos_foo
```

**File:** `Makefile`
```makefile
SHELL := bash
SSH_KEY    ?= ~/.ssh/id_ed25519.pub

build:
	exordos build -c exordos/exordos.yaml -i $(SSH_KEY) -f

deploy:
	exordos deploy -e output

install:
	exordos em elements install output/manifests/foo-aas.yaml

wheel:
	python -m build --wheel

publish-wheel: wheel
	cp dist/exordos_foo-*.whl /srv/exordos-local-repo/simple/

lint:
	tox -e ruff-check

format:
	tox -e ruff

test:
	tox -e py312

functional:
	tox -e py312-functional

typecheck:
	tox -e mypy
```

> **No `--manifest-var` flags:** the build toolchain resolves the DP image and the PyPI wheel via URNs (`{{ images.* }}`, `{{ artifacts.* }}`), so `REPOSITORY`/`INDEX_URL` Makefile variables and `--manifest-var repository=...` / `--manifest-var index_url=...` are no longer needed. The wheel is built by `build_wheel.sh` (declared in `exordos.yaml`'s `artifacts:` block), not by a separate `make wheel` step during `make build`.

---

## Step 8: CI/CD Workflows

### 8.1 Unit Tests + Lint

**File:** `.github/workflows/tests.yaml`

```yaml
name: tests

on:
  push:
  pull_request:
    types: [opened]

jobs:
  Lint:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: astral-sh/setup-uv@v5
      - run: uv tool install tox --with tox-uv
      - run: tox -e ruff-check

  Tests:
    runs-on: ubuntu-24.04
    strategy:
      matrix:
        python-version: ["3.10", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - uses: astral-sh/setup-uv@v5
      - run: uv tool install tox --with tox-uv
      - run: tox -e py${{ matrix.python-version }}
```

### 8.2 Functional Tests

**File:** `.github/workflows/func_tests.yaml`

See `exordos_mail/.github/workflows/func_tests.yaml` for the full working example. The pattern is:
1. Bootstrap exordos_core
2. Clone `exordos_metapaas` (runtime dependency)
3. Run `prepare_env.py` — builds artifacts, installs elements, waits for mail API
4. Run `tox -e py3.12-functional`
5. On failure: drop into upterm debug session

---

## Step 9: Deployment & Testing Locally

### Build & Deploy (one step)

The fastest local dev loop is `make build` followed by `exordos deploy`, which serves the local `output/` directory over an in-process HTTP server and installs the element directly into the current realm — no `exordos push` or published repository needed:

```bash
cd exordos_<name>

# Build DP image + manifest + PyPI wheel (wheel is built by build_wheel.sh
# via the artifacts: block in exordos.yaml, no separate make wheel needed)
make build

# Deploy the build to the current realm in one step (serves output/ locally,
# installs directly — no push). Equivalent: exordos deploy -e output
make deploy
```

`exordos deploy` is the recommended way to iterate during development. With `--repository <name>` it switches to push mode (push the build to a configured repository first, then install) — useful when deploying to a remote realm that can't reach your local machine. See `exordos_core/docs/app-developer-guide/deploy.md` for the full reference.

### Build & Install (explicit)

The two-step equivalent, useful when you want to control each phase or install from an already-pushed repository:

```bash
make build

# Install element in core (from the local manifest path)
make install
```

> `make publish-wheel` is only needed if you want the wheel on a local pip index for ad-hoc `pip install` outside the build. The `make build` + `make deploy`/`make install` flow resolves the wheel via the `{{ artifacts.pypi_package }}` URN and does not require a published index.

### Bootstrap CI environment locally

```bash
python exordos_<name>/tests/functional/prepare_env.py \
  --metapaas-dir ../exordos_metapaas \
  --project-dir . \
  --output-dir /tmp/<name>-build \
  --endpoint http://10.20.0.2:11010 \
  --username admin --password <pass>

# Use env vars printed by prepare_env.py, then:
tox -e py312-functional
```

---

## Checklist for New PaaS

- [ ] Repo structure: `exordos/{images,manifests}/`, `etc/{exordos_metapaas,systemd}/`, `exordos_<name>/controlplane/`, `exordos_<name>/dataplane/`, `exordos_<name>/migrations/`, `exordos_<name>/tests/`
- [ ] DP image: `dp_install.sh` (build) + `dp_bootstrap.sh` (first boot); `exordos.yaml` references both via `profile` + `script`
- [ ] `exordos.yaml`: **two** deps — plugin package (`/opt/exordos_metapaas`) + runtime (`/opt/exordos_metapaas_runtime`); two element entries (manifest-only + manifest+image); image entry has an `artifacts:` block with `build_wheel.sh` → `pypi_package` (`../dist/*.whl`)
- [ ] `exordos/images/build_wheel.sh`: self-contained wheel build script (copy from `metapaas_demo` or `exordos_observability`)
- [ ] `controlplane/dm/models.py`: `ModelWithUUID` + `ModelWithProject` + `orm.SQLStorableMixin`; Version model with `TargetResourceMixin`; child resources with `_touch_parent` + `_maybe_hash_password` if salted hashes
- [ ] `controlplane/api/controllers.py`: `PolicyBasedController` + `BaseResourceControllerPaginated`; `__policy_service_name__` + `__policy_name__` match manifest permission names
- [ ] `field_permissions`: hides secrets (`HIDDEN`), marks read-only fields (`RO`) like `status`, `ipsv4`
- [ ] `controlplane/api/routes.py`: route controller wiring
- [ ] `controlplane/infra/services/builder.py`: `CoreInfraBuilder` subclass (nodes, keys, config regen)
- [ ] `controlplane/paas/dm/models.py`: PaaS-layer `Instance` view mixing in `InstanceWithDerivativesMixin` + `DependenciesActiveReadinessMixin` with `get_readiness_dependencies()` returning `RI("<slug>_instance_iaas", self.uuid)` (see §3.1b — prevents FK-violation rollback race)
- [ ] `controlplane/paas/services/builder.py`: `PaaSBuilder` subclass (payload assembly, DP facts sync)
- [ ] `definition.py` (package root): `PaaSDefinition` subclass; `element_name` matches element registration name (e.g., `"foo-aas"`, **not** `"foo"`)
- [ ] `dataplane/driver.py`: `CapabilityDriver` subclass; registered via `gcl_sdk_universal_agent` entrypoint; `dump_to_dp()` uses `_write_file_atomic` (reload only on change); `restore_from_dp()` reads real disk state (not empty defaults)
- [ ] `migrations/`: DB migration files for all tables
- [ ] `pyproject.toml`: two entrypoints (`exordos_metapaas_paas` + `gcl_sdk_universal_agent`); `migrations/*.py` in package-data
- [ ] Manifest `<name>-aas.yaml.j2`: `$metapaas.types` (with correct `element_name`, `package: "{{ artifacts.pypi_package }}"`) + `$core.iam.permissions` + `$core.iam.permissionbinding` + `$<element_name>.types.<slug>.versions` (with `image: "{{ images.exordos_metapaas_<name>_dp }}"`)
- [ ] Manifest `example_<name>.yaml.j2`: declarative example instance for testing
- [ ] Unit tests: model fields, driver logic
- [ ] Functional tests: full E2E with API client fixtures and DP propagation polling
- [ ] Makefile + tox.ini: standard tooling (`build/deploy/install/wheel/test/functional/lint/typecheck`); `build` target has **no** `--manifest-var` flags (URNs resolve images + wheel); `deploy` target runs `exordos deploy -e output`
- [ ] CI workflows: `tests.yaml` (lint + unit) + `func_tests.yaml` (E2E)

---

## Common Pitfalls

1. **Permission name mismatch**: `__policy_service_name__ + "." + __policy_name__ + "." + action` must exactly match `name:` in `$core.iam.permissions`. A typo means 403 Forbidden.

2. **Wrong project in permissionbinding**: Always bind to the metapaas project (`4d657461-0000-0000-0000-000000000002`), not to a user project. Users get access by being granted owner in the metapaas project.

3. **Read-only fields in CREATE payload**: Omit `status`, `ipsv4`, and any field marked `read_only=True` or `Permissions.RO` from the create payload — they auto-populate or are set by orchestration.

4. **Hiding secrets**: Use `Permissions.HIDDEN` for passwords/tokens — they are never returned by the API. The field can still be set on CREATE/UPDATE but is stripped from all responses.

5. **DP image reference**: Use `image: "{{ images.exordos_metapaas_<name>_dp }}"` in the version catalog, where `<name>_dp` is the DP image `name:` from `exordos.yaml` with dashes → underscores. Do **not** hardcode `{{ repository }}/{{ name }}/{{ version }}/images/...` URLs — the build toolchain resolves the image via the `images.*` URN. The old `--manifest-var repository=...` Makefile flag is gone.

6. **Plugin package reference**: Use `package: "{{ artifacts.pypi_package }}"` in `$metapaas.types`, backed by the `artifacts:` block in `exordos.yaml` (see Step 5). Do **not** use `package: "exordos_<name>"` + `index_url: "{{ index_url | default('') }}"` — the build toolchain builds the wheel via `build_wheel.sh` and resolves it via the `artifacts.*` URN, so `index_url` and the `--manifest-var index_url=...` Makefile flag are no longer needed. (`index_url` is still supported on the `PaaSType` model for ad-hoc named-package installs from a private index, but the recommended build flow does not use it.)

7. **Version `description` field**: The version resource's `description` must contain the metapaas project UUID — this is how the core-agent version filter knows which versions belong to which project.

8. **`exordos_metapaas` is not on PyPI**: `uv sync` inside the DP image will fail unless you first build a local wheel. In `dp_install.sh`:
   ```bash
   uv build --wheel --out-dir /tmp/<name>-wheels/ /opt/exordos_metapaas_runtime
   uv sync --find-links /tmp/<name>-wheels/
   ```
   Also add `/opt/exordos_metapaas_runtime` as a second dep in `exordos.yaml`.

9. **`element_name` vs `slug` in namespace**: The version resource namespace is `$<element_name>.types.<slug>.versions`, where `element_name` comes from `PaaSDefinition.element_name` (and must match the element's registered name). If `slug = "foo"` but `element_name = "foo-aas"`, the namespace is `$foo-aas.types.foo.versions` — **not** `$foo.types.foo.versions`. A mismatch causes "Namespace was not found" when core tries to resolve the version reference.

10. **pip simple index missing package subdir** (only relevant for ad-hoc named-package installs via `index_url`): When PluginReconciler runs `pip install exordos_<name>` from a local index, it expects `<index_url>/<package-name>/index.html` to exist. If only the `.whl` file is present without the subdir+index.html, the install will 404. Create the subdir and link the wheel:
    ```bash
    mkdir -p /srv/exordos-local-repo/simple/exordos-paas-<name>
    # ... generate index.html with link to the .whl
    ```
    The recommended build flow (`package: "{{ artifacts.pypi_package }}"`) sidesteps this entirely — the wheel is resolved via URN, not via a simple index.

11. **Test isolation in functional tests**: Create a fresh instance per test session, not per test class. Use session-scoped fixtures. Clean up instances in fixture teardown.

12. **`restore_from_dp()` returning empty defaults causes infinite service restarts**: The DP
    agent calls `restore_from_dp()` to read actual state, then compares with target state. If
    `restore_from_dp()` sets fields to empty defaults (e.g. `self.accounts = {}`) even when
    real data exists on disk, actual≠target every cycle → `dump_to_dp()` fires every cycle →
    service reloads/restarts in a loop. Every field must be read from disk. See `MailInstance.restore_from_dp()` for the reference pattern (`_read_exim4_passwd()`).

13. **Unconditional `systemctl reload` in `dump_to_dp()` causes restart loops**: Calling reload
    unconditionally makes the agent restart the service on every reconciliation cycle (~3s). Use
    `_write_file_atomic` (read existing file, compare, skip write if identical) and only call
    reload/restart when the function returns `True` (content actually changed).

14. **Salted hash fields generate new salt on every core-agent cycle**: The core-agent stores the
    original plaintext from the create request in the orch target and re-applies it on every cycle.
    `sha512_crypt.hash(plaintext)` produces a different hash each time (random salt) → DB changes
    every cycle → DP diff → reload loop. Use `property.old_value` to verify the plaintext against
    the stored crypt hash before re-hashing. See `MailAccount._maybe_hash_password()` for the
    reference pattern.

15. **Version upgrade: set `version` field, not just `status`**: `PaaSType.update()` automatically
    resets `status=NEW` when `version` or `package` changes. The PluginReconciler then compares
    the installed dist version against `plugin.version` and reinstalls only if they differ. Setting
    `status=NEW` directly without changing `version` will result in the reconciler marking the
    plugin ACTIVE immediately (no reinstall) if the dist version already matches.

16. **PaaS derivative persisted before the dataplane agent registers (FK violation)**: if the
    PaaS-layer `Instance` model mixes in only `InstanceWithDerivativesMixin` (no readiness gate),
    the builder can try to persist `ua_target_resources` before the DP agent has registered in
    `ua_agents`. That hits a foreign-key violation on `ua_target_resources.agent_uuid →
    ua_agents.uuid` and rolls back the **entire** reconciliation transaction — including any
    child-resource work done in the same tick — leaving the instance stuck in `IN_PROGRESS`. This
    is a **race** (depends on IaaS reaching `ACTIVE` before the first builder tick), so it can pass
    in dev and fail on a slower stand. Fix: mix in `DependenciesActiveReadinessMixin` and implement
    `get_readiness_dependencies()` returning `RI("<slug>_instance_iaas", self.uuid)` (see §3.1b).
    The `RI` kind must match the infra-layer instance model's `get_resource_kind()`.

---

## Reference Implementations

- **exordos_mail** (`exordos_mail`) — **primary reference**: instances + accounts, DKIM, Exim4, functional tests. First complete end-to-end example of the plugin contract. Study `driver.py` for `_write_file_atomic`/`restore_from_dp`, `models.py` for `_maybe_hash_password`/`_touch_parent`.
- **exordos_observability** (`exordos_observability`) — two composable plugins (`victoriaaas` + `grafanaaas`) in one package; the first plugin to use `DependenciesActiveReadinessMixin` for PaaS→IaaS readiness gating (see §3.1b) and the URN-based manifest/build pattern (see Step 4 & Step 5). Study `*/controlplane/paas/dm/models.py` for the readiness mixin, and `exordos/manifests/*.yaml.j2` + `exordos/exordos.yaml` for the `images.*`/`artifacts.pypi_package` pattern.
- **exordos_s3** — production-grade, fully featured (instances, buckets, users, policies, access keys); pending migration to the plugin contract (phase 5).
