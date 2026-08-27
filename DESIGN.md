# Exordos MetaPaaS — High Level Design

> **PaaSes on this contract: `exordos_mail` (mail) and `exordos_s3` (s3).**

## Problem

Every PaaS (`exordos_s3`, `exordos_db`, `exordos_mail`) today is a separate repository
with its own control-plane node, its own pg-cluster (via dbaas), and its own copies of
`user_api`/`orch_api`/`status_api`/`gservice`. ~70 % of each PaaS codebase is duplicated
boilerplate.

## Target Architecture

**Single CP runtime** that hosts any number of PaaS types as installable Python plugins. A
new PaaS = a plugin package, not a new CP node. Creating an instance still brings up
**dataplane nodes only** — the CP itself never grows.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    exordos-metapaas (CP node)                       │
│                                                                     │
│  mp-user-api  ──  /v1/types/<slug>/  (per-plugin, dynamic mount)   │
│  mp-orch-api  ──  gcl_sdk universal orch API (generic)             │
│  mp-status-api ─  gcl_sdk universal status API (generic)           │
│  mp-gservice  ──  InfraScheduler + UAgent + PluginReconciler        │
│                   + per-plugin InfraBuilder + PaaSBuilder           │
│  mp-core-agent ─  db-back agent; [models]/[filters] generated       │
│                   from plugin registry (render_config)              │
│                                                                     │
│  Shared Postgres  (persistent disk; slug-prefixed tables per PaaS)  │
│  Plugin packages  (pip-installed at runtime, no rebuild)            │
└─────────────────────────────────────────────────────────────────────┘
         │                              │
         ▼ reconcile NodeSet/Config     ▼ dp agent
    Core compute                  DP nodes (per-PaaS image)
```

## Key Decisions

| Decision | Choice |
|---|---|
| Architecture | **A — single runtime + plugins** (not gcl_sdk library extension) |
| CP entity storage | **Real per-slug tables** in one embedded Postgres; migrations supplied by each plugin |
| Plugin versioning | **Independent** — each PaaS upgraded separately; migration tracking via shared `ra_migrations` (UUID ids) |
| Installing a new PaaS | **Without rebuilding metapaas** — pip-install at runtime; plugin entrypoint becomes discoverable immediately |
| Second copy of a PaaS | **YAGNI** — invariant `(deployment_id, slug)`; second copy = second element install or second slug |
| Contract versioning | **Not introduced** — compatibility ensured by gcl_sdk + plugin package version pins; added only when a real incompatibility arises |

## Plugin Contract — `PaaSDefinition`

A plugin is a Python package that registers a `PaaSDefinition` subclass via the
`exordos_metapaas_paas` entry-point group. The runtime discovers all installed definitions
at startup and wires them in automatically.

```python
class PaaSDefinition:
    slug: str            # "mail" — table prefix, /v1/types/<slug>/ mount point
    element_name: str    # "mailaas" — element name for EM kind construction

    def get_type_route(self) -> Route:
        """restalchemy Route for /v1/types/<slug>/ (models + controllers)."""

    def get_migrations_path(self) -> str | None:
        """Filesystem path to this plugin's migrations dir."""

    def get_builders(
        self,
        core_username, core_password, core_api_base_url, project_id,
    ) -> list:
        """Instantiate and return CP builder services (InfraBuilder, PaaSBuilder).
        Runtime adds InfraScheduler, UAgent, PluginReconciler unconditionally.
        """

    def get_agent_models(self) -> dict[str, str]:
        """core-agent [models] map: {subpath: "module:Model"}.
        subpath is relative to types.<slug>, e.g. "instances" or "instances.accounts".
        """

    def get_agent_filters(self) -> dict[str, str]:
        """core-agent [filters] map: {subpath: field_name}."""
```

EM kind formula: `em_<element_name>_types_<slug>_<subpath>` (dots → underscores).
Matches the kind Core emits for `$<element_name>.types.<slug>.<subpath>`.

The plugin author writes: models + validation, controllers, `infra_spec` logic (inside
`InfraBuilder.get_infra()`), `node_payload` logic (inside `PaaSBuilder._get_*` methods),
DP driver (in the DP image, via gcl_sdk entry-points), `dp_install.sh`/`dp_bootstrap.sh`,
one migration, IAM section in the PaaS element manifest.

## What gcl_sdk Provides (do not duplicate)

- `gcl_sdk.agents.universal` — universal agent, scheduler, builder engine (1746 lines),
  orch client (`DatabaseOrchClient`), core driver (`RestCoreCapabilityDriver`), dm models
  (`TargetResource`, `NodeEncryptionKey`, etc.).
- Generic orch API (`/v1/agents/<uuid>/actions/get_payload`) and status API
  (`/v1/kind/<name>/resources/`, `/v1/agents/`, `/v1/nodes/`) — metapaas mounts them as-is.
- `MigrationEngine` for cross-project migrations; shared `ra_migrations` tracking table.
- `PaaSBuilder` / `PaaSCollection`, `CoreInfraBuilder` / `InfraCollection` — abstract bases;
  heavy infra logic (node keys, shrink/grow, sizing, config regen) is **not** in gcl_sdk,
  currently per-plugin.
- IaaS primitives: `NodeSet`, `Node`, `Config`, `SetDisksSpec`, `NodeTarget`, `OnChangeShell`.
- `Service` / `ServiceDPTarget` / `CmdShell` — "run this on a DP node" model.

## Runtime Components

### user_api (`mp-user-api`)

Hosts `/v1/types/` with **dynamic route mounting**. On first request, `TypeRoute` calls
`discover_paas()` and `setattr`s each plugin's route onto itself; stale slugs are
`delattr`d. A `SIGUSR1` handler invalidates the cache so `install_paas` can trigger a
live route reload without a process restart.

```
/v1/
└── types/                        ← PaaSTypeController (CRUD for PaaSType)
    └── <slug>/                   ← plugin's Route (mounted dynamically)
        └── instances/...         ← plugin-specific hierarchy
```

### gservice (`mp-gservice`)

At startup: discovers plugins, calls `definition.get_builders()`, passes results to
`LaunchpadService` alongside the always-present `InfraScheduler`, `UAgent`, and
`PluginReconciler`.

```python
services = [InfraScheduler(), UAgent(**core_kwargs), PluginReconciler()]
for definition in discover_paas().values():
    services.extend(definition.get_builders(**core_kwargs))
```

### PluginReconciler

Watches `metapaas_paas_types` for rows with `status != ACTIVE`. For each:
1. Checks if the slug is already pip-installed (via a fresh subprocess).
2. If missing or version mismatch: runs `exordos-metapaas-install-paas` (no-restart mode).
3. Marks `status = ACTIVE`.
4. After all installs: sends SIGUSR1 to user_api workers + detached restart of gservice and
   core-agent via a transient systemd unit (so gservice restart doesn't kill itself).

`PaaSType.update()` resets `status = NEW` when `version` or `package` changes — triggering
the next reconciliation cycle.

### render_config (`exordos-metapaas-render-config`)

Generates two config files from the installed plugin registry:
- `exordos_metapaas.conf` — DB, IAM, API bind addresses, core credentials.
- `core_agent.conf` — `[models]` and `[filters]` for every resource kind across all plugins.

Built-in entry: `em_metapaas_types → PaaSType` (always subscribed, regardless of plugins).
Per-plugin entries built from `get_agent_models()` / `get_agent_filters()`.

Run at first boot and re-run by `install_paas` after each pip install. Idempotent: DB
password and project id are recovered from an existing config when env vars are absent.

### install_paas (`exordos-metapaas-install-paas`)

Imperative install primitive (also called by PluginReconciler):
```
pip install <spec>  →  apply plugin migrations  →  render_config  →  SIGUSR1 + restart
```
Accepts pip spec (name==version, path, URL). `--no-restart` flag for staging multiple
installs before a single restart.

## Data Model

```
metapaas_paas_types               (runtime table — not a plugin table)
  uuid, name (slug), element_name
  package, version, index_url     ← pip install spec (package is a wheel URN in the
                                    recommended build flow; version and index_url
                                    are optional and ignored for URN/URL packages)
  status                          ← NEW | ACTIVE (reset to NEW on version change)

<slug>_*                          (per-plugin tables; tablename must start with slug_)
  plugin's own migrations, tracked in shared ra_migrations by UUID
```

## Deploying MetaPaaS

One element (`exordos_metapaas`): single CP node + embedded Postgres on a persistent disk.

**Manifest exports:**
- `$metapaas.node` — the CP node reference for DNS/networking
- `$metapaas.project` — stable project UUID `4d657461-0000-0000-0000-000000000002`;
  PaaS consumer manifests import this instead of hardcoding.

**Bootstrap sequence (`cp_bootstrap.sh`):**
1. Wait for `/etc/exordos_init.txt` (Core config injection).
2. Move Postgres + `/etc/exordos_metapaas` + `/var/lib/exordos/exordos_metapaas` to
   the persistent data disk (`find_persistent_disk` / `migrate_to_persistent_*`).
3. Create embedded DB user/db (first boot only).
4. `render_config` → `exordos_metapaas.conf` + `core_agent.conf`.
5. Apply gcl_sdk migrations, then metapaas own migrations, then per-plugin migrations.
6. Seed CP node's RSA key into local DB (`ua_node_encryption_keys`).
7. `systemctl enable --now` all five services.

**Upgrade:**
- **Dataplane**: new PaaS image → InfraBuilder rolls DP nodes rolling/canary. CP untouched.
- **Plugin CP code**: new package version in `PaaSType` resource → PluginReconciler pip-installs →
  applies unapplied migrations → rolling reload. Independent of other plugins.
- **MetaPaaS core**: rebuild/roll CP image (rare; only for changes to the runtime itself).

**Why this is safe:**
- Tables namespaced by slug; migrations UUID-tracked independently per plugin.
- DP is autonomous — during CP rolling-restart, instances keep serving traffic.
- Packages arrive via the authenticated Core element-manager (same trust domain as images).
  Hard invariant: `tablename` must start with `f"{slug}_"`.

## DP Driver Idempotency (Invariant for All PaaS Plugins)

The DP agent calls the driver on every reconciliation cycle (~every few seconds):

- `dump_to_dp()` — compare before writing; call `systemctl reload` only on content change.
  Never unconditionally.
- `restore_from_dp()` — read **actual on-disk state**. Returning empty defaults when data
  exists → diff every cycle → restart loop.
- Salted hashes (bcrypt, sha512crypt) — use `property.old_value` to verify plaintext
  against the stored hash before re-hashing; a new salt each cycle causes a restart loop.

## PaaS→IaaS Readiness Gating (Invariant for All PaaS Plugins)

A PaaS-layer `Instance` model (in `controlplane/paas/dm/models.py`) must mix in
`DependenciesActiveReadinessMixin` (from `gcl_sdk.agents.universal.dm.models`) and
implement `get_readiness_dependencies()` returning the IaaS instance as a dependency:

```python
class FooInstance(
    models.FooInstance,
    ua_models.InstanceWithDerivativesMixin,
    ua_models.DependenciesActiveReadinessMixin,
):
    def get_readiness_dependencies(self):
        return (ua_models.RI("foo_instance_iaas", self.uuid),)
```

The `RI` kind must match the infra-layer instance model's `get_resource_kind()`.
This gates the PaaS builder from persisting the derivative (`ua_target_resources`)
until the IaaS instance is `ACTIVE` — i.e. until the compute set is provisioned
and the dataplane agent is (or is about to be) registered in `ua_agents`.

Without this gate the builder can try to persist the derivative before the agent
row exists in `ua_agents`, hitting a foreign-key violation on
`ua_target_resources.agent_uuid → ua_agents.uuid` that rolls back the entire
reconciliation transaction — including any child-resource work done in the same
tick — and leaves the instance stuck in `IN_PROGRESS`. This is a **race**
(depends on IaaS reaching `ACTIVE` before the first builder tick), so it can pass
on a fast stand and fail on a slow one. First discovered and fixed in
`exordos_observability` (M8); see `HOW_TO_BUILD_NEW_PAAS.md` §3.1b for the full
pattern and the verified reference implementations.

## Plugin Packaging

A plugin is packaged as a versioned Python distribution and specified in `PaaSType`.
The recommended build flow resolves both the wheel and the DP image via URNs
produced by the `exordos build` toolchain — no `--manifest-var` flags or hardcoded
repository URLs:

- **Wheel artifact URN** (recommended): `package = "{{ artifacts.pypi_package }}"` in
  the manifest, backed by an `artifacts:` block in `exordos.yaml` that runs
  `build_wheel.sh` and collects `../dist/*.whl`. The build toolchain turns this into
  a wheel URN the PluginReconciler resolves via Core.
- **DP image URN**: `image = "{{ images.exordos_metapaas_<name>_dp }}"` in the version
  catalog, where `<name>_dp` is the image `name:` from `exordos.yaml` with dashes →
  underscores. The build toolchain resolves this to the built image artifact.
- **pip name + version** (ad-hoc, optional): `package = "exordos-paas-mail"`,
  `version = "1.2.3"`, optionally with `index_url` pointing pip at a private index.
  Still supported on the `PaaSType` model for installs outside the common build flow.
- **URL/path** (ad-hoc, optional): `package = "https://repo.../exordos_paas_mail-1.2.3-py3-none-any.whl"`
  — any `.whl` or `.tar.gz` pip understands.

The DP driver ships separately inside the DP image (via gcl_sdk universal-agent entry-points).

## What Is Not Yet Implemented

| Gap | Impact |
|---|---|
| **Generic runtime bases** (`PaaSInstance`, `PaaSVersion`, generic `InfraBuilder`/`PaaSBuilder`) | Per-plugin boilerplate: each plugin writes its own full infra/paas builders. Currently in `exordos_mail`; not extracted into the runtime. |
| **Auto-IAM from `iam_actions`** | Each plugin declares its IAM permissions manually in its element manifest. No auto-generation from a plugin-supplied actions list. |
| **Plugin registry status_api endpoint** | No public API to enumerate installed PaaS types and their versions. `metapaas_paas_types` is only visible via the db-back agent. |

## Risk Register

| Risk | Mitigation |
|---|---|
| **Blast radius** — all PaaSes share one CP | Sharding / multiple metapaas instances as a future option. |
| **Plugin migration safety** in shared DB | Hard invariant: `tablename` must start with `slug_`. |
| **Dynamic route mounting** | Mitigated: `setattr` + entry-point loading already proven in gcl_sdk and s3 `app.py`. |
| **Runtime pip install** | Rolling-restart of workers instead of in-process module injection. |
| **Infra logic divergence** | s3 and db have diverged copies of node-key/shrink/sizing logic; extracting generic `InfraBuilder` into the runtime will require reconciling them. |
| **PaaS derivative persisted before DP agent registers** (FK violation race) | Mitigated: PaaS-layer `Instance` must mix in `DependenciesActiveReadinessMixin` gating on the IaaS instance being `ACTIVE` (see "PaaS→IaaS Readiness Gating" above). First hit in `exordos_observability` M8; `exordos_s3`/`metapaas_demo` predate the fix and should adopt the mixin. |

---

## Guides

📖 **[HOW_TO_BUILD_NEW_PAAS.md](HOW_TO_BUILD_NEW_PAAS.md)** — Step-by-step guide for building a new PaaS plugin
