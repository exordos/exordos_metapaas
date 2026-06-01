# Exordos MetaPaaS

**Single control-plane runtime hosting many PaaS types as installable plugins.**

Instead of deploying a new VM + database for each PaaS service (s3, mail, database, etc.), MetaPaaS runs one shared control-plane that manages them all. A new PaaS is an installable plugin, not a new control-plane node — only data-plane instances are launched per service instance.

## Quick Start

### Build

```bash
make build
# Produces: output/exordos-metapaas.raw.zst, output/manifests/metapaas.yaml
```

### Install to Exordos Core

```bash
exordos -e http://10.20.0.2:11010 -u admin -p <password> \
  ee install metapaas --version 0.0.7 --repository https://repo.exordos.com/exordos-elements
```

### Create a PaaS Instance

Once metapaas is deployed, install a plugin (e.g., s3aas):

```bash
exordos -e http://10.20.0.2:11010 -u admin -p <password> \
  ee install s3aas --version 0.0.1 --repository https://repo.exordos.com/exordos-elements
```

Then create an instance via the metapaas-cp REST API:

```bash
curl -X POST http://metapaas-cp:8080/v1/types/s3/instances \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <token>' \
  -d '{
    "name": "s3-prod",
    "version": "0.0.1",
    "bucket_name": "data",
    "encryption": "aes256"
  }'
```

## Documentation

### For Platform Users
- **[DESIGN.md](DESIGN.md)** — Architecture, why this approach, solved decisions

### For PaaS Plugin Developers
- **[HOW_TO_BUILD_NEW_PAAS.md](HOW_TO_BUILD_NEW_PAAS.md)** — Complete guide: 9 steps from architecture to deployment
  - Control Plane (CP): SQLAlchemy models, REST controllers, IAM setup
  - Data Plane (DP): Packer image config, health checks, systemd units
  - Build config, manifest template, tests, CI/CD
  - Examples: mail-aas (Postfix+Dovecot), database-aas patterns
  - Checklist + troubleshooting

### Reference Plugins
- **[../exordos_s3/](../exordos_s3/)** — Production s3aas plugin (validated, fully featured)
- **[../exordos_mail/](../exordos_mail/)** — Blueprint mail-aas plugin (validates framework is service-agnostic)
- **[./metapaas_demo/](./metapaas_demo/)** — Demo plugin (bundled in runtime, minimal example)

## Architecture

```
exordos_core (user-facing)
    ↓
exordos_metapaas-cp (single shared control-plane)
    ├─ user-api       (REST endpoints: /v1/types/<slug>/instances)
    ├─ orch-api       (orchestration, node lifecycle)
    ├─ status-api     (monitoring, node status)
    └─ PostgreSQL     (shared database for all plugins)
        ├─ s3_instances, s3_buckets, ...
        ├─ mail_instances, mail_users, ...
        └─ plugin_migrations (each plugin versions independently)
    ↓
data-plane nodes (s3-dp, mail-dp, db-dp, ...)
    ↓
actual services (RustFS, Postfix, PostgreSQL, ...)
```

**Key insight:** Plugin = pip package with CP code + manifest + DP image. No new CP VM, no new database — just install and go.

## Building Your First Plugin

1. **Read** [HOW_TO_BUILD_NEW_PAAS.md](HOW_TO_BUILD_NEW_PAAS.md)
2. **Copy** structure from [../exordos_mail/](../exordos_mail/) (simple, well-commented)
3. **Follow** the 9 steps: models → controllers → IAM → DP image → manifest → build → tests
4. **Validate** against the checklist
5. **Deploy** and test

## Development

### Unit Tests

```bash
tox -e py312
```

### Linting & Type Checking

```bash
tox -e ruff-check
tox -e mypy
```

### Full Test Matrix

```bash
tox
```

## Plugin Installation Mechanism

When a PaaS element is installed in exordos_core:

1. **PluginReconciler** (running on metapaas-cp) detects new element
2. **Fetches** the CP wheel from the pip index (registry URL from element spec)
3. **Installs** wheel on metapaas-cp with `pip install --upgrade`
4. **Discovers** the plugin via entry-point: `[project.entry-points."exordos_metapaas.plugins"]`
5. **Registers** REST routes, models, migrations
6. **Applies** any pending database migrations (cross-project safe via UUID-keyed tracking)
7. **Reloads** metapaas-cp services to pick up new routes

Data-plane nodes are provisioned per instance as the plugin's CP controllers orchestrate them.

## Troubleshooting

### Plugin not appearing in API

Check PluginReconciler logs on metapaas-cp:

```bash
exordos -e http://10.20.0.2:11010 -u admin -p <pass> \
  cn exec metapaas-cp -- \
  journalctl -u metapaas-plugin-reconciler -f
```

### Cannot create instance

Verify metapaas-cp is reachable and user has credentials:

```bash
curl -s http://metapaas-cp:8080/v1/types/ -H 'Authorization: Bearer <token>' | jq .
```

### Data-plane nodes stuck in CREATING

Check orchestration (orch-api) and agent logs on metapaas-cp:

```bash
exordos -e http://10.20.0.2:11010 -u admin -p <pass> \
  cn exec metapaas-cp -- \
  tail -f /var/log/orch-api.log
```

## Standards

- **Python:** 3.10+
- **Testing:** pytest, tox, coverage
- **Code style:** ruff (fmt + check), mypy
- **Database:** SQLAlchemy 2.0+, PostgreSQL 13+
- **Container format:** Packer → Zstandard-compressed raw image

## License

Proprietary — Exordos
