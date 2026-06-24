# Windmill: Workflows (use case)

> Local backup of <https://www.windmill.dev/use-cases/workflows>, captured via
> WebFetch on 2026-06-07. The fetcher condenses prose; for the authoritative
> data model see `flow-reference.md` (derived from the real OpenFlow spec).

## What is a workflow on Windmill?

A workflow is a multi-step process that chains scripts together to automate a
task end to end. Each step runs in its own isolated environment, with typed
inputs and outputs passed between steps automatically.

Windmill supports two approaches: **low-code flows** where you compose steps
visually as a DAG, and **workflows as code** where you orchestrate steps
programmatically. Both get retries, error handling and full observability out of
the box.

## Two types of workflows

### Low-code flows
Visual DAG composition in the flow editor: drag and drop branches, loops,
approval steps and error handlers. Each step can use a different language, with
full inspection of inputs/outputs at every node and restart from any step.

### Workflows as code
Define the entire workflow programmatically in TypeScript or Python, calling
other scripts as steps using native language control flow. Steps still run in
isolation with full observability (tracked in `workflow_as_code_status`).

## Built-in features
- Retries and error handling (per step and a flow-level failure handler).
- Approval steps (human-in-the-loop / suspend-resume).
- Triggers: webhooks, schedules, Kafka, Postgres CDC, SQS, MQTT, email,
  WebSockets.
- Branches and loops (parallel execution and iterative processing).
- Auto-generated UI for every workflow.

## Production-ready
- Immutable versioning and instant rollback.
- Real-time execution logs streamed as the workflow runs.
- Full audit trails (who ran what and when).
- Role-based access control with folder-level permissions.
- Git sync with GitHub/GitLab.

Claims to be the fastest workflow engine, scaling from a single node to
1,000-node Kubernetes clusters with auto-scaling and dedicated worker groups.
