# Code Architecture and Package Structure

Status: Draft
Last reviewed: 2026-08-25

## Purpose

This document defines package ownership, dependency direction, composition roots, and code-placement
rules under `src/trace_capture/`. See [System Architecture](./system.md) for process composition and
runtime flows, and [README](../../README.md) for user commands and environment variables.

Do not document layers or packages that do not exist as if they were current architecture. Update
this document in the same change when package ownership or dependency direction changes.

## High-level dependency direction

The repository does not enforce a separate framework-level domain layer. It separates application
behavior, external adapters, and delivery/composition responsibilities.

```mermaid
flowchart TD
    DELIVERY[cli web service]
    APP[agent planning runtime automation workspace candidate_generation]
    CONTRACTS[contracts and owner models]
    ADAPTERS[auth providers tools capture composition tunnel transport]
    EXTERNAL[model provider Appium filesystem browser launchd cloudflared]

    DELIVERY --> APP
    DELIVERY --> ADAPTERS
    APP --> CONTRACTS
    APP --> ADAPTERS
    ADAPTERS --> CONTRACTS
    ADAPTERS --> EXTERNAL
```

The direction is:

1. `cli/`, `web/`, and `service/` translate user input and compose concrete dependencies.
2. `agent/`, `planning/`, `runtime/`, `automation/`, `candidate_generation/`, and `workspace/` own
   application behavior and state transitions.
3. `auth/`, `providers/`, `search/`, `tools/`, `capture/`, `composition/`, `tunnel/`, and
   `transport/` implement external or technical boundaries.
4. `contracts/` and owner-package models define typed data across boundaries.

Application packages must not depend on concrete UI or infrastructure objects such as Typer,
FastAPI, Textual widgets, or Appium drivers. Accept required behavior through protocols and connect
implementations at composition roots.

## Directory structure

```text
src/trace_capture/
├── agent/          # agent loop, context, TUI, REPL, standalone sessions
├── auth/           # OAuth flow and credential persistence
├── automation/     # durable campaigns, queue, producer, worker and review lifecycle
├── candidate_generation/  # context assembly, one-call candidate production and the offline image stage
├── capture/        # Appium/XCUITest capture and artifact validation
├── cli/            # installed Typer entry points and composition roots
├── composition/    # deterministic offline image-layer validation and composition
├── config/         # environment-backed runtime settings
├── contracts/      # versioned cross-boundary Pydantic contracts
├── marketing/      # Cloudflare task bridge, durable inbox/outbox, and local loop proof
├── planning/       # marketing context to scene recipe
├── providers/      # model transport, catalog and image generation adapters
├── search/         # text/image search contracts and external provider adapters
├── runtime/        # GenerateOne and TraceRun orchestration, journal and replay
├── service/        # service bootstrap, worker hosting, launchd and status
├── tools/          # model-visible tools, registry and approval boundaries
├── transport/      # shared HTTP and JSON transport primitives
├── tunnel/         # optional public-tunnel process boundary
└── web/            # FastAPI routes and static workspace shell
```

## Package ownership

| Package | Owns | Must not own |
| --- | --- | --- |
| `agent/` | Conversation history, context projection/compaction, tool loop, and TUI/REPL session control | Provider HTTP details or native capture |
| `auth/` | OAuth login/refresh and protected credential storage | Agent conversation policy or Web member authentication |
| `automation/` | Campaign state, variation production, queue idempotency, due claims, leases, worker-result validation, and review transitions | HTTP routes or artifact-generation implementations |
| `candidate_generation/` | Context-document loading, the assembled generation instruction, strict-JSON parsing with one retry, all-or-nothing candidate writing through a store protocol, and the offline candidate image run | HTTP routes, provider transport details, native capture, composition algorithms, or candidate review transitions |
| `capture/` | Appium endpoints/sessions, Simulator/Appium readiness, Trace setup entry, component collection, and provenance validation | Scene planning or final composition policy |
| `cli/` | Typer input validation, exit codes, and dependency composition | State machines or business transitions |
| `composition/` | Offline layer validation, transparency/path constraints, system-UI normalization, and deterministic PNG composition | Appium navigation, provider calls, or Image Model composition |
| `config/` | Conversion of environment variables into typed runtime settings | Secret persistence or product state |
| `contracts/` | Versioned capture, composition, generation, run, and model-tool descriptor contracts | File, network, or database access |
| `marketing/` | Cloudflare task/callback contracts, Mac inbox/outbox, bridge orchestration, task-handler ports, and local account-loop proof | Credential values, HTTP routes, or channel-specific policy |
| `planning/` | Side-effect-free conversion from `MarketingContextBundle` to `SceneRecipe` and image-search query | Image generation, capture, or persistence |
| `providers/` | Provider request/response mapping, model catalog, and image-generation adapters | UI state or workspace persistence |
| `search/` | Text/image search contracts, provider selection, and external adapters | Model-visible dispatch or workspace state |
| `runtime/` | GenerateOne/TraceRun orchestration, capability order, searched background plus clean system UI and deterministic composition boundary, journals, replay, locks, and artifact validation | CLI output or HTTP routing |
| `service/` | Loopback listener, workspace bootstrap, automation-worker hosting, launchd, and service status | Web schemas or queue transitions |
| `tools/` | Executable tools, registry, approval, workspace paths, and bounded output | Provider loop or TraceRun state transitions |
| `transport/` | Shared HTTP client and JSON transport types | Provider-specific meaning |
| `tunnel/` | cloudflared process and emitted public-URL boundary | The full local-service lifecycle |
| `web/` | FastAPI auth/member-invite/context/asset/campaign/candidate/chat/generation/queue/session routes, TUI-compatible chat command adapter, HTTP error mapping, and static shell | Durable transitions or provider details |
| `workspace/` | Workspace/member identity, code hashes/versions, shared context, asset metadata, and private sessions | Automation queue or model calls |

## Composition root

Compose concrete dependencies at these entry points.

| Entry point | Composition responsibility |
| --- | --- |
| `cli/agent.py` | OAuth, model client, tool registry, tool context, context runtime, memory/session store, TUI/REPL |
| `agent/factory.py` | Shared `ToolContext` and `AgentSession` composition for CLI and Web |
| `cli/generate.py` | context bundle, image generator, capture adapter, `GenerateOneRunner` options |
| `capture/factory.py` | Native capture-adapter selection by device kind |
| `cli/trace_run.py` | run store, capture port, compose port, CLI error mapping |
| `web/app.py` | workspace/queue stores, session codec, chat factory, candidate generator, focused routers, static shell |
| `candidate_generation/factory.py` | Per-run HTTP client, OAuth store, provider and image clients, context directory, and shipped fixture paths for candidate production and the image stage |
| `service/runtime.py` | listener, FastAPI app, production generation runner, automation worker, tunnel shutdown |
| `service/worker.py` | queue scheduler, `GenerateOneWorker`, service-owned artifact roots and provider/capture adapters |
| `cli/marketing.py` | local simulation, external pull-bridge, and opt-in installed candidate-pipeline dependency composition |
| `cloudflare/src/index.js` | hosted HTTP API, Workflow, Durable Object, D1, Queue, and R2 composition |

Do not start new dependencies through global singletons or import side effects. Construct them at
composition time and give an explicit lifespan or context manager ownership of shutdown.

`marketing/` owns the Cloudflare Queue task contract, durable Mac inbox/outbox, bridge orchestration,
task-handler port, optional candidate-journey adapter, and local end-to-end control-plane proof. The
candidate adapter may invoke the provider-neutral ports in `candidate_generation/` and the existing
workspace review store; Cloudflare response shapes do not enter either package. Channel-specific
production handlers depend inward on the marketing contract rather than putting their credentials
or response shapes into `automation/` or `workspace/`.

`cloudflare/` is a separate deployment composition root. Its Worker owns HTTP authorization and D1
registry APIs; `MarketingWorkflow` owns durable orchestration; `MarketingAccountAgent` owns one
account's private memory. Pure transition rules stay in `cloudflare/src/state-machine.js` so they can
be tested outside the Workers runtime.

## Code placement rules

### Provider features

- Put provider-specific request, response, and error mapping in `providers/`.
- Reuse `transport/` for shared HTTP behavior.
- Put a provider-neutral port in the package that owns the calling behavior.
- Expose model/provider selection through composable settings in `config/settings.py`.

### Search providers

- Put model-visible text/image search execution adapters in `tools/`.
- Put search contracts and external adapters such as DDGS and Brave under `search/text/` or
  `search/image/`.
- Keep text and image result models separate because URL, thumbnail, and source-page semantics differ.

### Model tools

- Put execution and descriptors in `tools/`.
- Keep the shared `ToolDescriptor` wire contract in `contracts/tools.py`; each tool creates values.
- Register each tool once in `tools/registry.py`.
- Define approval for mutating behavior.
- Restrict file and command paths to the selected workspace.
- Do not hard-code tool names separately in providers or the TUI.

### Candidate generation

- Keep the context-document contract, instruction assembly, and strict-JSON parsing in
  `candidate_generation/`.
- Accept the provider client through the `ModelClient` protocol and the store through the
  `CandidateWriter` protocol; compose both in `candidate_generation/factory.py`.
- Keep the Web layer limited to authentication, typed-error-to-status mapping, and response shaping.
- v1 is script assembly: one provider call, no tool loop and no search.
- The image stage owns only orchestration: it calls the `providers/` image port for the background
  and drives `runtime/`'s `TraceRunRunner` with the offline `LocalArtifactCapturePort` and
  `LocalComposePort`. Do not reimplement capture, staging, or composition inside this package.
- Candidate journey transitions stay in `workspace/`; the image runner writes through the
  `CandidateImageStore` protocol and never edits status columns itself.

### Web APIs

- Add one focused router module under `web/`.
- Put request/response models in `web/schemas.py` or a focused owner schema module.
- Reuse `agent/tui_commands.py` when a Web command has a TUI equivalent; do not create a second
  command vocabulary in the browser.
- Implement durable behavior in the owning `workspace/` or `automation/` package.
- Keep `web/app.py` limited to router composition.

### Workspace data

- `workspace/` owns workspace, member, context, asset, and private-session state; Web admin identity is anchored to the owner ID in `service.json`.
- `automation/` owns campaign lifecycle, variation numbering, scheduling, leases, runs, and review state.
- Do not put HTTP cookie or response shapes in database models.
- Let store methods own SQLite transactions and optimistic revisions.

### Generation stages

- Define versioned input and result contracts first.
- Put orchestration in `runtime/`.
- Run external behavior through adapters behind protocols.
- When adding a TraceRun capability, update transitions, replay, journal, idempotency, and artifact
  validation together.

### Service features

- `service/` owns process, listener, launchd, and background-worker lifecycles.
- Do not start workers through a FastAPI import side effect.
- Keep `create_app()` composable and testable without a worker.
- Attach production workers explicitly in `service/runtime.py`.

### Hosted marketing loop

- Keep the cross-runtime task and callback schema versioned in `marketing/models.py` and mirror it at
  the Worker boundary.
- Add a Mac task kind through `TaskExecutor`/`TaskHandler`; do not branch on channel credentials in
  the inbox store.
- Keep queue acknowledgement after durable inbox insertion and callback delivery after durable
  outbox insertion.
- Encode Queue HTTP-pull envelopes as JSON text, keep task completion event types unique per task,
  and normalize transport exceptions at the Cloudflare client boundary.
- Keep account registry data in D1 and account-private learned memory in the named Durable Object.
- Let D1's partial unique index own the one-active-run-per-account invariant; API and Cron checks are
  explanatory fast paths, not the concurrency authority.
- Treat a new account, locale, schedule, instruction revision, or credential reference as data.
- Require code review for a new adapter, task kind, state edge, or retry rule.

## Cross-package rules

- Do not repeat one business rule across CLI, TUI, and Web routes.
- Delivery layers translate typed results from the owning service, store, or runtime.
- Do not move helpers into catch-all modules such as `utils.py` or `common.py` without a real shared
  owner.
- Keep a type in its owning package when only one package uses it. Move only stable cross-boundary
  contracts into `contracts/`.
- Convert external exceptions into typed errors or results at the owner boundary.
- Do not put secrets, access codes, or credentials in descriptors, exceptions, or ordinary logs.
- Let the owning store, journal, or lease enforce concurrency rather than repeating checks in callers.

## Test placement

Name files under `tests/` after the production package and user boundary they protect. See
[Testing and Verification](../development/testing.md) for scope selection.

- Protect pure planning, contracts, and state transitions with fast unit tests.
- Test SQLite stores with real temporary databases and transaction/revision boundaries.
- Execute Typer commands and exit codes for CLI tests.
- Exercise real FastAPI routes, auth scope, and conflict responses for Web tests.
- Use isolated state roots for service lifecycle, worker, health, and shutdown tests.
- Validate artifact paths, digests, provenance, and output files for capture/composition tests.
- Put Mac bridge, account isolation, and local loop tests in `tests/marketing/`; put pure Worker state
  tests in `cloudflare/test/`.

Use mocks at external provider and Appium boundaries. A test that only inspects calls inside a mock
does not prove the owning boundary.

## Document maintenance

Update this document in the same change when:

- a package is added, removed, moved, or renamed;
- package responsibility or ownership changes;
- allowed dependency direction changes;
- a composition root changes;
- a type or contract changes owner; or
- a code-placement rule or exception is introduced.

Also update `docs/architecture/system.md` when process composition, runtime flow, persisted state, or
an external-system boundary changes.
