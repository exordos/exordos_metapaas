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
│  ├─ Plugin reconciler (watches metapaas project, installs plugins on CP)
│  └─ Base schema (PaaS instance, version, status, nodes)
│
└─ metapaas_<name> (your new plugin)
   ├─ exordos/exordos.yaml (build config: DP image + manifest)
   ├─ exordos/images/dp_install.sh (runs during DP image build)
   ├─ exordos/images/dp_bootstrap.sh (runs on first DP node boot)
   ├─ exordos/manifests/<name>-aas.yaml.j2 (rendered template, deployed to core)
   ├─ exordos_paas_<name>/ (Python CP code)
   │  ├─ controllers.py (REST endpoints)
   │  ├─ models.py (restalchemy models)
   │  ├─ definition.py (PaaSDefinition contract)
   │  ├─ driver.py (CapabilityDriver for DP agent)
   │  └─ tests/
   ├─ pyproject.toml, tox.ini, Makefile (standard exordos tooling)
   └─ .github/workflows/ (CI: tests.yaml + func_tests.yaml)
```

---

## Step 1: Create the Repository Structure

```bash
cd ~/exo
mkdir metapaas_<name>
cd metapaas_<name>

# Create directories
mkdir -p exordos/{manifests,images}
mkdir -p exordos_paas_<name>/{migrations,tests/{unit,functional}}
mkdir -p etc/systemd
mkdir -p .github/workflows
```

There is **no separate `<name>-dp/` directory** — DP build scripts live under `exordos/images/`.

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

**File:** `exordos_paas_<name>/models.py`

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
    ua_models.TargetResourceMixin,   # needed so the runtime can track this as a resource
):
    __tablename__ = "foo_versions"

    image = properties.property(types.String(max_length=2048))


class FooInstance(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithProject,          # gives project_id → multitenancy
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "foo_instances"

    status = properties.property(
        types.Enum([s.value for s in FooStatus]),
        default=FooStatus.NEW.value,
    )
    ipsv4 = properties.property(types.TypedList(types.String(max_length=15)), default=lambda: [])
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

### 3.2 Controllers

**File:** `exordos_paas_<name>/controllers.py`

Controllers wire together IAM enforcement and REST routing. Use `gcl_iam.controllers` mixins — they implement the `__policy_service_name__.<resource>.<action>` check against Core IAM automatically.

```python
from gcl_iam import controllers as iam_controllers
from restalchemy.api import constants
from restalchemy.api import controllers as ra_controllers
from restalchemy.api import field_permissions as field_p
from restalchemy.api import resources as ra_resources

from exordos_paas_foo import models


class FooController(ra_controllers.RoutesListController):
    """Entry point: /v1/types/foo/"""
    __TARGET_PATH__ = "/v1/types/foo/"


class FooVersionController(
    iam_controllers.PolicyBasedWithoutProjectController,  # no project scope — shared catalog
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "exordos_foo"   # ← must match permission name prefix in manifest
    __policy_name__ = "foo_version"           # ← exordos_foo.foo_version.read

    __resource__ = ra_resources.ResourceByRAModel(
        model_class=models.FooVersion,
        convert_underscore=False,
        process_filters=True,
    )


class FooInstanceController(
    iam_controllers.PolicyBasedController,      # project-scoped; checks project_id
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "exordos_foo"   # ← exordos_foo.foo_instance.*
    __policy_name__ = "foo_instance"

    __resource__ = ra_resources.ResourceByRAModel(
        model_class=models.FooInstance,
        convert_underscore=False,
        process_filters=True,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status":  {constants.ALL: field_p.Permissions.RO},
                "ipsv4":   {constants.ALL: field_p.Permissions.RO},
            },
        ),
    )
```

How permissions map to manifest:
- `__policy_service_name__ = "exordos_foo"` + `__policy_name__ = "foo_instance"` → framework checks `exordos_foo.foo_instance.<action>` in Core IAM
- That permission must exist in Core and be bound to the user's role — this happens in the **manifest** (Step 4)

### 3.3 PaaS Definition

**File:** `exordos_paas_<name>/definition.py`

This is the plugin's entry point class. The metapaas runtime loads it via the `exordos_metapaas_paas` entrypoint group:

```python
import os

from exordos_metapaas.registry import PaaSDefinition
from exordos_paas_foo import routes


class FooDefinition(PaaSDefinition):
    slug = "foo"
    element_name = "foo-aas"   # ← MUST match element registration name in manifest

    def get_type_route(self):
        return routes.FooRoute

    def get_migrations_path(self):
        return os.path.join(os.path.dirname(__file__), "migrations")

    def get_builders(self, core_username, core_password, core_api_base_url, project_id):
        from exordos_paas_foo.infra_builder import CoreInfraBuilder
        from exordos_paas_foo.infra_models import FooInstance as InfraFooInstance
        from exordos_paas_foo.paas_builder import FooInstanceBuilder
        from exordos_paas_foo.paas_models import FooInstance as PaaSFooInstance

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
            "versions": "exordos_paas_foo.models:FooVersion",
            "instances": "exordos_paas_foo.infra_models:FooInstance",
        }

    def get_agent_filters(self):
        return {
            "versions": "description",        # versions filtered by project id in description
            "instances": "project_id",
        }
```

> **Critical:** `element_name` must match exactly the element's registered name (what you pass to `exordos em elements install`). The framework derives the version namespace as `$<element_name>.types.<slug>.versions`. If `element_name = "foo"` but the element is registered as `"foo-aas"`, the namespace won't be found and the DP version catalog will be empty.

### 3.4 Plugin Entrypoints in pyproject.toml

```toml
# The metapaas runtime discovers your PaaSDefinition via this group
[project.entry-points."exordos_metapaas_paas"]
foo = "exordos_paas_foo.definition:FooDefinition"

# The core agent discovers your CapabilityDriver via this group
[project.entry-points."gcl_sdk_universal_agent"]
FooCapabilityDriver = "exordos_paas_foo.driver:FooCapabilityDriver"
```

When the `foo-aas` element is installed in exordos_core, the runtime:
1. Fetches the wheel from `index_url` in the manifest
2. `pip install`s it on metapaas-cp
3. Loads `FooDefinition` via entrypoint
4. Calls `get_type_route()` → mounts routes at `/v1/types/foo/`
5. Calls `get_migrations_path()` → applies pending DB migrations
6. Calls `get_builders(...)` → instantiates infra + paas builders

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
  # pip-installs the package from index_url at runtime on metapaas-cp.
  $metapaas.types:
    foo:
      name: "foo"
      element_name: "foo-aas"      # ← must match the element registration name
      package: "exordos_paas_foo"
      index_url: "{{ index_url | default('') }}"
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

  # Dataplane version catalog. `description` carries the metapaas project id
  # for the core-agent version filter.
  # Namespace: $<element_name>.types.<slug>.versions  ← note: element_name, not slug
  $foo-aas.types.foo.versions:
    foo_v1:
      name: "foo_v1"
      description: "4d657461-0000-0000-0000-000000000002"
      image: "{{ repository | default('https://repo.exordos.com/exordos-elements') }}/{{ name }}/{{ version }}/images/exordos-metapaas-foo-dp.raw.zst"

exports:
  foo_v1:
    link: "$foo-aas.types.foo.versions.$foo_v1"
```

---

## Step 5: Build Configuration

**File:** `exordos/exordos.yaml`

Two deps are always needed: the plugin package itself and `exordos_metapaas` runtime (which is not on PyPI and must be built from source in `dp_install.sh`).

```yaml
build:

  deps:
    # Plugin package: copied into the image so dp_install.sh can run uv sync
    - dst: /opt/exordos_metapaas
      path:
        src: ../../metapaas_<name>
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

    # Image entry: same manifest + DP image build
    - manifest: manifests/foo-aas.yaml.j2
      images:
        - name: exordos-metapaas-foo-dp
          format: GEN_IMG_FORMAT_CORE=zst
          profile: exordos_base
          script: images/dp_install.sh
          override:
            disk_size: "6G"
            use_backing_file: true
```

---

## Step 6: Testing Structure

### 6.1 Unit Tests

**File:** `exordos_paas_<name>/tests/unit/test_driver.py`

Test driver logic and model behaviour in isolation (no live stand needed):

```python
import pytest
from unittest.mock import patch, MagicMock
from exordos_paas_foo.driver import FooCapabilityDriver


def test_driver_creates_config_from_accounts():
    driver = FooCapabilityDriver()
    # ... test specific driver logic
```

### 6.2 Functional Tests

**File:** `exordos_paas_<name>/tests/functional/test_<name>_e2e.py`

Test end-to-end against a live stand using the real CP/DP path. Follow the pattern established in `metapaas_mail` and `metapaas_s3`:

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

Copy from `metapaas_mail` or `metapaas_s3` and adapt package names.

**File:** `pyproject.toml`
```toml
[project]
name = "exordos_paas_foo"
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
foo = "exordos_paas_foo.definition:FooDefinition"

[project.entry-points."gcl_sdk_universal_agent"]
FooCapabilityDriver = "exordos_paas_foo.driver:FooCapabilityDriver"

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
exordos_paas_foo = ["migrations/*.py"]
```

**File:** `tox.ini`
```ini
[tox]
envlist = py312, ruff-check, mypy, py312-functional
isolated_build = True

[testenv]
package = wheel
extras = test
commands = pytest exordos_paas_foo/tests/unit {posargs}

[testenv:py{310,311,312,313,314}-functional]
extras = test
setenv =
    EXORDOS_ENDPOINT = {env:EXORDOS_ENDPOINT:http://10.20.0.2:11010}
    METAPAAS_USERNAME = {env:METAPAAS_USERNAME:metapaas}
    METAPAAS_PASSWORD = {env:METAPAAS_PASSWORD:}
    EXORDOS_USERNAME = {env:EXORDOS_USERNAME:admin}
    EXORDOS_PASSWORD = {env:EXORDOS_PASSWORD:}
commands = pytest exordos_paas_foo/tests/functional {posargs}

[testenv:ruff-check]
extras = ruff
commands = ruff check exordos_paas_foo

[testenv:ruff]
extras = ruff
commands = ruff format exordos_paas_foo

[testenv:mypy]
extras = mypy
commands = mypy exordos_paas_foo
```

**File:** `Makefile`
```makefile
SHELL := bash
SSH_KEY    ?= ~/.ssh/id_ed25519.pub
REPOSITORY ?= http://10.20.0.1:8080/exordos-elements
INDEX_URL  ?= http://10.20.0.1:8080/simple/

build:
	exordos build -c exordos/exordos.yaml -i $(SSH_KEY) -f \
		--manifest-var repository=$(REPOSITORY) \
		--manifest-var index_url=$(INDEX_URL)

install:
	exordos em elements install output/manifests/foo-aas.yaml

wheel:
	python -m build --wheel

publish-wheel: wheel
	cp dist/exordos_paas_foo-*.whl /srv/exordos-local-repo/simple/

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

See `metapaas_mail/.github/workflows/func_tests.yaml` for the full working example. The pattern is:
1. Bootstrap exordos_core
2. Clone `exordos_metapaas` (runtime dependency)
3. Run `prepare_env.py` — builds artifacts, installs elements, waits for mail API
4. Run `tox -e py3.12-functional`
5. On failure: drop into upterm debug session

---

## Step 9: Deployment & Testing Locally

### Build & Serve

```bash
cd metapaas_<name>

# Build DP image + manifest
make build

# Publish wheel to local pip index
make publish-wheel

# Install element in core
make install
```

### Bootstrap CI environment locally

```bash
python exordos_paas_<name>/tests/functional/prepare_env.py \
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

- [ ] Repo structure: `exordos/images/`, `exordos/manifests/`, `exordos_paas_<name>/`, `etc/systemd/`
- [ ] DP image: `dp_install.sh` (build) + `dp_bootstrap.sh` (first boot); `exordos.yaml` references both via `profile` + `script`
- [ ] `exordos.yaml`: **two** deps — plugin package (`/opt/exordos_metapaas`) + runtime (`/opt/exordos_metapaas_runtime`); two element entries (manifest-only + manifest+image)
- [ ] `models.py`: `ModelWithUUID` + `ModelWithProject` + `orm.SQLStorableMixin`; Version model with `TargetResourceMixin`
- [ ] `controllers.py`: `PolicyBasedController` + `BaseResourceControllerPaginated`; `__policy_service_name__` + `__policy_name__` match manifest permission names
- [ ] `field_permissions`: hides secrets (`HIDDEN`), marks read-only fields (`RO`) like `status`, `ipsv4`
- [ ] `definition.py`: `PaaSDefinition` subclass; `element_name` matches element registration name (e.g., `"foo-aas"`, **not** `"foo"`)
- [ ] `routes.py`: route controller wiring
- [ ] `driver.py`: `CapabilityDriver` subclass; registered via `gcl_sdk_universal_agent` entrypoint
- [ ] `migrations/`: DB migration files for all tables
- [ ] `pyproject.toml`: two entrypoints (`exordos_metapaas_paas` + `gcl_sdk_universal_agent`); `migrations/*.py` in package-data
- [ ] Manifest `<name>-aas.yaml.j2`: `$metapaas.types` (with correct `element_name`) + `$core.iam.permissions` + `$core.iam.permissionbinding` + `$<element_name>.types.<slug>.versions`
- [ ] Manifest `example_<name>.yaml.j2`: declarative example instance for testing
- [ ] Unit tests: model fields, driver logic
- [ ] Functional tests: full E2E with API client fixtures and DP propagation polling
- [ ] Makefile + tox.ini: standard tooling (`build/install/wheel/test/functional/lint/typecheck`)
- [ ] CI workflows: `tests.yaml` (lint + unit) + `func_tests.yaml` (E2E)

---

## Common Pitfalls

1. **Permission name mismatch**: `__policy_service_name__ + "." + __policy_name__ + "." + action` must exactly match `name:` in `$core.iam.permissions`. A typo means 403 Forbidden.

2. **Wrong project in permissionbinding**: Always bind to the metapaas project (`4d657461-0000-0000-0000-000000000002`), not to a user project. Users get access by being granted owner in the metapaas project.

3. **Read-only fields in CREATE payload**: Omit `status`, `ipsv4`, and any field marked `read_only=True` or `Permissions.RO` from the create payload — they auto-populate or are set by orchestration.

4. **Hiding secrets**: Use `Permissions.HIDDEN` for passwords/tokens — they are never returned by the API. The field can still be set on CREATE/UPDATE but is stripped from all responses.

5. **DP image URL**: Use `/{{ name }}/{{ version }}/images/` in the manifest (both `name` and `version` are template variables), not hardcoded paths.

6. **Missing `index_url`**: Leave `index_url: "{{ index_url | default('') }}"` in the manifest so PluginReconciler can find the CP wheel on private registries.

7. **Version `description` field**: The version resource's `description` must contain the metapaas project UUID — this is how the core-agent version filter knows which versions belong to which project.

8. **`exordos_metapaas` is not on PyPI**: `uv sync` inside the DP image will fail unless you first build a local wheel. In `dp_install.sh`:
   ```bash
   uv build --wheel --out-dir /tmp/<name>-wheels/ /opt/exordos_metapaas_runtime
   uv sync --find-links /tmp/<name>-wheels/
   ```
   Also add `/opt/exordos_metapaas_runtime` as a second dep in `exordos.yaml`.

9. **`element_name` vs `slug` in namespace**: The version resource namespace is `$<element_name>.types.<slug>.versions`, where `element_name` comes from `PaaSDefinition.element_name` (and must match the element's registered name). If `slug = "foo"` but `element_name = "foo-aas"`, the namespace is `$foo-aas.types.foo.versions` — **not** `$foo.types.foo.versions`. A mismatch causes "Namespace was not found" when core tries to resolve the version reference.

10. **pip simple index missing package subdir**: When PluginReconciler runs `pip install exordos_paas_<name>` from your local index, it expects `<index_url>/<package-name>/index.html` to exist. If only the `.whl` file is present without the subdir+index.html, the install will 404. Create the subdir and link the wheel:
    ```bash
    mkdir -p /srv/exordos-local-repo/simple/exordos-paas-<name>
    # ... generate index.html with link to the .whl
    ```

11. **Test isolation in functional tests**: Create a fresh instance per test session, not per test class. Use session-scoped fixtures. Clean up instances in fixture teardown.

---

## Reference Implementations

- **metapaas_s3** — production-grade, fully featured (instances, buckets, users, policies, access keys)
- **metapaas_mail** — simpler reference (instances + accounts); good starting point for new plugins
