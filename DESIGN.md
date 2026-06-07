# Exordos MetaPaaS — Design

> Phases 1–3 complete. First PaaS built on this contract: **mail** (exordos_mail).
> First migration target: **s3**.

## Goal

Currently every PaaS (`exordos_s3`, `exordos_db`, future `exordos_mail`) is a separate
repository that carries **its own** control-plane deployment: a dedicated CP-VM, a dedicated
pg-cluster (via dbaas), dedicated `user_api`/`orch_api`/`status_api`/`gservice`.
~70% of each PaaS codebase is copy-pasted boilerplate.

MetaPaaS is a **single CP runtime** that multi-tenantly hosts many PaaS types. A new PaaS =
an installable plugin (`PaaSDefinition`), not a new CP node. Creating an instance brings up
**dataplane nodes only**. This eliminates both the boilerplate and N extra CP-VMs with their DBs.

## Locked decisions

| Decision point | Choice |
|---|---|
| Architecture | **A — single runtime + plugins** (not extending gcl_sdk as a library) |
| CP entity storage | **Real per-paas tables in one DB**, table prefix = `slug`, migrations supplied by the plugin |
| Plugin versioning | **Independent** — each PaaS is updated/migrated separately (UUID migrations in the shared restalchemy `ra_migrations`) |
| Installing a new PaaS | **Without rebuilding metapaas** — via the core element-manager + an agent inside metapaas (or direct admin-API). Plugin = a runtime-installable artifact, loaded via entry-point (same as drivers already do) |
| Second copy of a PaaS | **YAGNI** — only the invariant `(deployment_id, slug)`; second copy = redeploy the element or use a second slug |

## What gcl_sdk already provides (do not duplicate) — verified against cloned code

- `gcl_sdk.agents.universal` — universal agent service, scheduler service, **universal builder
  engine** (`services/builder.py`, 1746 lines — shared engine for Paas/Infra builders),
  orch client (`DatabaseOrchClient`), core driver (`RestCoreCapabilityDriver`), dm models
  (`TargetResourceKindAwareMixin`, `InstanceWithDerivativesMixin`, `Resource`,
  `TargetResource`, `NodeEncryptionKey`).
- **Generic agent-facing API is already in gcl_sdk** (per-paas does NOT write this): `agents/universal/orch_api`
  (`/v1/agents/<uuid>/actions/get_payload`), `agents/universal/status_api`
  (`/v1/kind/<name>/resources/`, `/v1/agents/`, `/v1/nodes/`), `agents/universal/api`
  (`BaseSdkResourceController`, middlewares, crypto). metapaas simply mounts them.
- **Ready-made plugin mechanism**: entry-point group `EP_UNIVERSAL_AGENT="gcl_sdk_universal_agent"`
  + `common/utils.load_from_entry_point(group, name)` + config-driven driver list
  (`caps_drivers`/`facts_drivers`, each reads its own ini section). metapaas mirrors this
  pattern for the CP side (its own EP group for `PaaSDefinition`).
- **Cross-project migrations**: `common/utils.MigrationEngine` applies migrations from an external
  package file. Tracking — shared restalchemy table `ra_migrations` (id = UUID).
- `gcl_sdk.paas.services.builder.PaaSBuilder` / `PaaSCollection` — **abstract** bases
  (create/actualize/schedule are stubs; all generic hook machinery is already here).
- `gcl_sdk.infra.services.builder.CoreInfraBuilder` / `InfraCollection` — also **abstract**
  base (149 lines). NOTE: heavy logic (node keys, shrink/grow, sizing nodeset,
  config regeneration) is currently **copied in each PaaS** (s3 234 lines, db has its own copy),
  it is NOT in gcl_sdk → metapaas generic `InfraBuilder` genuinely deduplicates it.
- `gcl_sdk.infra.dm.models` — IaaS primitives: `NodeSet`, `Node`, `Config`,
  `SetDisksSpec`, `NodeTarget`, `TextBodyConfig`, `OnChangeShell`.
- `gcl_sdk.paas.dm.services` — `Service` / `ServiceDPTarget` / `CmdShell`: already model
  "run this service on a DP node", reuse for launching DP instead of reinventing.

## Current PaaS layers and their fate

| Layer | Files (s3) | Fate |
|---|---|---|
| user_api boilerplate | `user_api/api/{app,routes,versions}.py`, `cmd/user_api.py` | → metapaas runtime |
| **CP entities** | `user_api/dm/models.py`, `api/controllers.py` | → **plugin** (almost no changes) |
| orch_api | all of `orch_api/`, `cmd/orch_api.py` | **already generic in gcl_sdk** — metapaas only mounts + cmd |
| status_api | all of `status_api/` | **already generic in gcl_sdk** — metapaas only mounts + cmd |
| infra | `infra/dm/models.py`, `infra/services/builder.py` | heavy logic deduplicated in runtime (not in gcl_sdk); **disk layout + config-template → plugin** |
| paas | `paas/dm/models.py`, `paas/services/builder.py` | scheduling → runtime; **payload assembly → plugin** |
| **agent driver** | `agent/universal/drivers/s3.py` | → **plugin** (ships in the DP image via gcl_sdk) |
| gservice | `services/gservice.py`, `cmd/gservice.py` | → metapaas runtime |
| common/cmd/systemd/migrations | | runtime + auto-IAM from registry |

## `PaaSDefinition` plugin contract

Registered via an entry-point group (analogous to `gcl_sdk_universal_agent`):

```python
class PaaSDefinition:                      # loaded via entry-point (like gcl_sdk drivers)
    slug: str                    # "s3"  — table prefix, path /v1/types/<slug>/
    entity_models: list          # restalchemy SQL models for CP entities + validation
    routes: Route                # route tree under /v1/types/<slug>/
    migrations_path: str         # migrations directory (CREATE TABLE, tablename starts with f"{slug}_")
    iam_actions: list            # actions → metapaas auto-generates permissions/bindings

    def infra_spec(instance) -> InfraSpec:    # nodeset (cpu/ram/disk/replicas/image) + config-template(s)
        ...
    def node_payload(instance) -> dict:       # payload for the DP node (from paas builder _get_*)
        ...
    dp_driver: str               # ref to the DP driver (entry-point EP_UNIVERSAL_AGENT, in DP image)
    dp_image_kind: str           # DP node image type
```

> `contract_version`/`schema_version` are deliberately NOT introduced now (premature). Rough
> compatibility is already ensured by pinning gcl_sdk version and the plugin package version.
> We will add them when a real incompatibility arises.

The PaaS author writes **only**: models + validation, `infra_spec`, `node_payload`,
DP driver (mostly already in gcl_sdk), `dp_install.sh` + `dp_bootstrap.sh` scripts for the DP image, one migration.

### DP driver idempotency invariant

The DP agent calls the driver on every reconciliation cycle (~every few seconds). The driver
**must** be idempotent:

- `dump_to_dp()` — compare new config with existing file before writing; only reload the
  service if content actually changed (`_write_file_atomic` pattern). **Never call
  `systemctl reload/restart` unconditionally.**
- `restore_from_dp()` — read the **actual on-disk state** into `self`. Returning empty
  defaults (e.g. `self.accounts = {}`) when real data exists on disk makes actual≠target
  every cycle → `dump_to_dp()` fires every cycle → service restarts in a loop.
- CP models with salted hashes (bcrypt, sha512crypt): if core-agent stores plaintext in the
  orch target and re-applies it every cycle, a naive `hash(plaintext)` generates a new salt
  each time → DB hash changes → DP sees diff → restart loop. Fix: use
  `property.old_value` to verify plaintext against the stored hash before re-hashing.

The plugin is packaged as a **versioned Python package**. In the PaaSDefinition resource it is
specified in one of two ways (both installed via `pip` with no extra logic):
- **pip name + version** from the configured pip index — `package: "exordos-paas-s3"`, `version: "1.2.3"`;
- **full path/URL to the artifact** `.whl` / `.tar.gz` (the same nginx repo as images, or any URL) —
  `package_url: "https://repo.exordos.com/.../exordos_paas_s3-1.2.3-py3-none-any.whl"`.

The DP driver ships separately — inside the DP image.

## Generic bases in metapaas runtime

- `PaaSInstance` — common fields `name/status/project_id/version(image)/cpu/ram/disk_size/nodes_number/ipsv4`.
- `InstanceChildModel` — cascade `touch_parent → instance.update(force=True)` for DP resync.
- `PaaSVersion` — image registry `name→image`, keyed by slug.
- Generic `InfraBuilder` — node encryption keys, shrink/grow, sizing nodeset,
  per-node config regeneration; takes disk layout + config content from `infra_spec`.
- Generic `PaaSBuilder` — scheduling (1 entity → 1 agent by uuid); payload from `node_payload`.
- Plugin loader + dynamic route mounting under `/v1/types/<slug>/` +
  aggregating migration-runner + auto-IAM.

## Lifecycle: installing and upgrading PaaSes

Principle: **installing/updating a PaaS never rebuilds the metapaas image.** Rebuilding the
CP image is only needed to update the metapaas core itself (rare). This follows the existing
exordos pattern — PaaSes are already installed as core elements.

### Artifacts per PaaS
1. **PaaS package** — Python package implementing `PaaSDefinition` (models/validation/
   `infra_spec`/`node_payload`/controllers/migrations/IAM). Specified as **pip name+version** from
   the index **or** a direct **URL/path to `.whl`/`.tar.gz`**. Installed via `pip`.
2. **DP image** — separate artifact, contains the DP driver (via universal agent).
3. **PaaS element** — core manifest that, instead of a CP-VM, declares a `PaaSDefinition` resource:
   `slug`, `package`+`version` (pip) or `package_url` (whl/tar.gz),
   DP image/version ref, `contract_version`.

### Install flow (declarative, primary)
```
exordos em elements install paas-s3.yaml
  → core saves PaaSDefinition resource in element-manager
  → metapaas agent (universal-agent capability driver, kind=paas_definition)
       reconciles resource: pip install package (by index name or by url to whl/tar.gz)
       into plugins environment on sys.path (its entry-point becomes visible in new processes)
       → runs unapplied plugin migrations (MigrationEngine, tracked in shared ra_migrations)
       → auto-generates IAM from iam_actions
       → triggers rolling-restart of API workers (mp-user-api/orch/status),
         which re-enumerate installed plugins on startup
  → /v1/types/s3/ is live, no s3 CP nodes
```
This is the "agent inside metapaas that pulls info from element-manager." The direct
metapaas-API admin endpoint is a thin wrapper over the same installer (for dev).

### Upgrade
- **Dataplane**: new `PaaSVersion` (new image) → infra-builder rolls nodes rolling/canary.
  CP entities are not touched.
- **Plugin CP code**: new package version in PaaSDefinition resource → agent `pip install` →
  unapplied plugin migrations → rolling-restart of workers. Independent of other PaaSes.
- **metapaas core**: rebuild/roll out CP image (rare).

### Why this is safe
- Tables are namespaced by `slug`; migrations use UUID ids in the shared `ra_migrations`, each
  plugin's `_depends` chains are self-contained → updating one PaaS does not touch others.
- Dataplane is autonomous → during CP rolling-restart, instances keep serving traffic
  (≤ sub-second blip on management-API only).

### Registry
The installer maintains a lock of installed PaaSes (slug → package version, applied migrations,
DP image ref), exposed via `status_api`.

### Trust
Packages arrive via the authenticated core element-manager — the same trust domain as images
that are today fetched and booted under root. Package execution is acceptable in this model.
Hard invariant: plugin `tablename` must start with `f"{slug}_"`.

## Deploying metapaas

One element `exordos_metapaas`: its own CP node + one pg cluster. Services:
`mp-user-api`, `mp-orch-api`, `mp-status-api`, `mp-gservice`, `mp-bootstrap`.
CP image is built once. DP image — per-paas, standardized via universal agent.

**DB persistence (like dbaas CP):** metapaas stores CP state in the DB, so the DB
**must** reside on a second persistent disk, not on root (root is recreated on image update).
The CP node declares a second disk `label: data`; bootstrap via
`lib_bootstrap.sh` (`find_persistent_disk` / `prepare_persistent_disk` /
`migrate_to_persistent_stop_start /var/lib/postgresql postgresql@18-main`) moves
postgres + `/etc/exordos_metapaas` + `/var/lib/exordos/exordos_metapaas` to the data disk.
For the spike DB — embedded postgres on the CP node (no dbaas); password is generated locally
(`generate_secure_password`) and persisted in config on the data disk.

## Phased plan

1. ✅ **`exordos_metapaas` skeleton** — runtime: orch_api/status_api/agent-API from gcl_sdk,
   user_api host, gservice, common, systemd. Own manifest: CP node + pg.
2. ✅ **`PaaSDefinition` contract** — loader via entry-point, dynamic route mounting,
   migration-runner (`MigrationEngine`), auto-IAM.
3. ✅ **PaaS installer (PluginReconciler)** — watches `metapaas_paas_types`, pip-installs
   missing/upgraded packages, applies migrations, restarts workers. Version-change detection:
   `PaaSType.update()` resets `status=NEW` when `version`/`package` changes.
4. **Generic bases** — `PaaSInstance`, `InstanceChildModel`, `PaaSVersion`, generic
   `InfraBuilder`/`PaaSBuilder`. *(Partially done in exordos_mail; needs extraction into runtime.)*
5. **`exordos_paas_s3` plugin** — migrate `user_api/dm+api`, `infra`, `paas`, driver,
   migration; package as artifact + paas element. Delete boilerplate from s3 repo.
6. **Contract validation** — existing s3 functional tests against metapaas.
   **`exordos_mail` is the first complete "new PaaS = plugin" proof** (instances, accounts,
   DKIM, Exim, functional tests).

## Risks

- **Blast radius** — one CP deployment for all PaaSes → availability coupling.
  Mitigation later: sharding / multiple metapaas instances.
- **Trust in plugin migrations** in shared DB → strictly require `tablename` starts with `slug_`.
- **Dynamic route/restalchemy model registration** — risk reduced: `setattr` mounting
  and entry-point loading already exist in code (s3 `app.py`, gcl_sdk `cmd/universal_agent.py`).
- **Runtime package installation** (`pip install` → migrations → rolling-restart) — spike in phase 3;
  rolling-restart of workers instead of in-process module injection for simplicity/safety.
- **Infra logic deduplication** — node keys/shrink/sizing currently have diverged copies in s3 and db;
  when merging into generic `InfraBuilder`, reconcile divergences into one behavior.

## s3 → plugin migration map (phase 5)

| Source (exordos_s3) | Destination |
|---|---|
| `user_api/dm/models.py` (S3Instance/Bucket/User/Policy/AccessKey/Version) | plugin: entity_models (Instance/Child inherit generic bases) |
| `user_api/api/controllers.py` + `routes.py` | plugin: routes |
| `migrations/0000-init-*.py` | plugin: migrations |
| `infra/dm/models.py` `get_infra()` + `RUSTFS_CONF_TEMPLATE` | plugin: `infra_spec()` |
| `paas/services/builder.py` `_get_buckets/users/policies/access_keys` | plugin: `node_payload()` |
| `agent/universal/drivers/s3.py` | plugin: dp_driver (in DP image) |
| `s3aas.yaml.j2` IAM boilerplate (~150 lines) | runtime: auto-IAM from `iam_actions` |
| `app/cmd/gservice/orch/status/common` | delete (present in runtime) |

---

## Guides

📖 **[HOW_TO_BUILD_NEW_PAAS.md](HOW_TO_BUILD_NEW_PAAS.md)** — Step-by-step guide for building a new PaaS plugin

From CP/DP architecture to a complete working example:
- Control Plane: models, controllers, IAM
- Data Plane: install/bootstrap scripts, systemd, health checks
- Manifest, build config, tests, CI/CD
- Examples: mail-aas (Postfix+Dovecot), database-aas patterns
- Validation checklist, common mistakes
