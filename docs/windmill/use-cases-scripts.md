# Windmill — Scripts (use case)

> Local backup of <https://www.windmill.dev/use-cases/scripts>, captured via
> WebFetch on 2026-06-07. The fetcher condenses prose; for the authoritative
> data model see `flow-reference.md`.

## What is a script on Windmill?

A script is a function in any language that you write, deploy and monitor
directly on Windmill. Windmill handles the runtime, the dependencies, the UI and
the API. You just write the logic.

Each script automatically gets an auto-generated form, webhook endpoints and
real-time logs. Scripts can run on demand or through built-in triggers, and can
be composed into workflows and apps.

## Standalone or building block
Scripts work on their own as API endpoints, scheduled jobs or shareable tools.
They can also be composed into workflows, data pipelines, internal apps and more.

## Key features
- **Auto-generated UI**: each script gets a form derived from its argument
  signature.
- **API endpoints**: scripts become synchronous and asynchronous endpoints.
- **Webhooks** and **built-in triggers**: schedules, Kafka, Postgres CDC, SQS,
  MQTT, email, WebSockets.
- **Versioning**: immutable, hash-addressable versions.
- **Observability**: execution logs with inputs, outputs, duration and status.

## Deployment options
- Windmill UI editor (LSP + AI assistance).
- Local development with the CLI (`wmill`) or the VS Code extension.
- Git sync with GitHub/GitLab.

## Composition
Scripts function as standalone tools or as building blocks for workflows (chained
with approval steps, parallel branches and conditional logic), internal apps,
data pipelines (ETL with parallel execution) and tool-calling AI agents.

## Languages
TypeScript (`bun`/`deno`/`nativets`), Python (`python3`), Go, Bash/PowerShell,
SQL dialects (postgresql, mysql, bigquery, snowflake, mssql, oracledb, duckdb),
GraphQL, PHP, Rust, Ansible, C#, Nu, Java, Ruby, R — "20+ languages".
