# Production Runbook

This runbook covers direct operation of Domain-Specific Paper Harness on Google
Cloud. Commands assume the repository root and PowerShell. Never place secret
values in source, Terraform variables, command arguments, logs, or generated
artifacts.

## Safety invariants

- Reuse the configured Google Cloud project. Do not create a duplicate project.
- Default all regional resources to `asia-southeast1`.
- Inspect billing, enabled APIs, quotas, policies, and existing resources before
  any cloud change.
- Do not create Cloud SQL, a fixed-cost load balancer, a paid VM, Kubernetes, or
  another recurring-cost resource without explicit authorization.
- Keep Web/API behind Cloud Run IAP and the owner allowlist. Keep GROBID private.
- Use immutable Artifact Registry digests and fixed enabled secret versions.
- Run Alembic explicitly. The API never migrates at startup.
- Run the Daily pipeline through its Cloud Run Job, never through FastAPI.
- Review every Terraform plan before applying it.
- Do not grant temporary project or service-account roles from deployment
  scripts.

## Authentication and preflight

Set non-secret identifiers for the session:

```powershell
$Project = "<existing-project-id>"
$Region = "asia-southeast1"
$Owner = "<owner-google-account>"
$StateBucket = "<existing-terraform-state-bucket>"
$VarFile = "<absolute-path-to-untracked-production.tfvars>"
```

Inspect local and cloud context:

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
python --version
uv --version
node --version
corepack pnpm --version
docker version
terraform version
gcloud version
gcloud auth list
gcloud config list
gcloud projects describe $Project
gcloud billing projects describe $Project
gcloud services list --enabled --project=$Project
gcloud run services list --project=$Project --region=$Region
gcloud run jobs list --project=$Project --region=$Region
gcloud scheduler jobs list --project=$Project --location=$Region
gcloud secrets list --project=$Project
```

The active `gcloud` project must exactly match `$Project`. Interactive login or
consent is a user action; stop if it is required.

## Terraform state

The backend bucket is a one-time cloud resource. First inspect whether it
already exists. When creation is authorized, use:

```powershell
scripts/bootstrap-terraform-state.ps1 `
  -BucketName $StateBucket `
  -ProjectId $Project `
  -Location $Region
```

The first call is read-only. Review its output, then rerun with `-Apply` only if
creating the bucket is authorized. The script requires uniform bucket-level
access, public-access prevention, object versioning, and a seven-day soft-delete
window.

## Production database

Production requires PostgreSQL 15 or newer with pgvector. The URL must:

- use `postgresql+psycopg://`;
- require TLS with `sslmode=require`, `verify-ca`, or `verify-full`;
- include a database, user, and password;
- use a direct or session-affine endpoint; and
- remain server-side only.

The application does not provision or substitute a database provider.

Before a production migration, create a provider-native backup and verify that
the provider reports it as complete. Test restoration only into a distinct
non-production database using the provider's supported procedure. Never expose
the production URL in command arguments or restore over production as a test.

## Optional Demo schema bootstrap and synchronization

The public Demo data boundary is optional and does not alter the production
Web, Daily, Scheduler, IAP, or production database credential. It requires the
current managed PostgreSQL account to support `CREATE SCHEMA`, `CREATEROLE`, and
column-level grants, with the existing `vector` extension installed.

The one-time, idempotent bootstrap reads credentials only from the environment:

```powershell
$env:DATABASE_URL = "<database-owner-url>"
$env:DATABASE_SCHEMA = "public"
$env:DEMO_SYNC_DB_PASSWORD = "<new-sync-role-password>"
$env:DEMO_READ_DB_PASSWORD = "<new-read-role-password>"
uv run --frozen paper-harness bootstrap-demo-schema
Remove-Item Env:DEMO_SYNC_DB_PASSWORD
Remove-Item Env:DEMO_READ_DB_PASSWORD
Remove-Item Env:DATABASE_URL
Remove-Item Env:DATABASE_SCHEMA
```

The command creates `paper_harness_demo_sync` and
`paper_harness_demo_read`, migrates `demo` to the application head, applies the
explicit production column grants, and verifies that the read role cannot read
`public` or write `demo`. It has no prompt or per-paper approval step. If the
provider rejects one of these real boundaries, stop; do not grant either Demo
role a production-wide permission.

Construct two TLS-required, direct/session-affine URLs for those roles using the
same host and database. Set `DATABASE_SCHEMA=demo` for both consumers. After a
reviewed Terraform apply creates the optional secret containers, add each URL
through `scripts/add-secret-version.ps1`; never store either URL in source or a
Terraform value.

Run an initial synchronization with only the restricted sync URL:

```powershell
$env:APP_ENV = "production"
$env:DATABASE_SCHEMA = "demo"
$env:DATABASE_URL = "<demo-sync-url>"
uv run --frozen paper-harness sync-demo-schema
Remove-Item Env:DATABASE_URL
Remove-Item Env:DATABASE_SCHEMA
Remove-Item Env:APP_ENV
```

Synchronization copies all current canonical publication history and periodic
reports with their paper/analysis/evidence/comparison/graph/trend/lineage
closure. It does not copy raw parsed passages, embeddings, cursors, or backfill
bookkeeping. A failed transaction or revision mismatch leaves the preceding
Demo snapshot unchanged.

When `deploy_demo_sync_automation=true`, configure these non-secret repository
variables from the Terraform outputs:

```text
GCP_PROJECT_ID
GCP_DEMO_SYNC_WORKLOAD_IDENTITY_PROVIDER
GCP_DEMO_SYNC_SERVICE_ACCOUNT
GCP_DEMO_SYNC_DATABASE_SECRET_ID
GCP_DEMO_SYNC_DATABASE_SECRET_VERSION
```

The final value is the fixed enabled numeric sync-secret version. The separate
post-CI workflow runs after a successful `main` push. Its status is visible but
is not a production required check or deployment dependency. The read secret is
reserved for the later public Demo runtime and is not accessible to the sync
identity.

## Secret versions

Terraform creates secret containers. Add values only through the bounded stdin
helper:

```powershell
$env:DATABASE_URL = "<production-url>"
scripts/add-secret-version.ps1 `
  -SecretId paper-harness-database-url `
  -ProjectId $Project `
  -ValueEnvironmentVariable DATABASE_URL
Remove-Item Env:DATABASE_URL

$env:DEEPSEEK_API_KEY = "<key>"
scripts/add-secret-version.ps1 `
  -SecretId paper-harness-deepseek-api-key `
  -ProjectId $Project `
  -ValueEnvironmentVariable DEEPSEEK_API_KEY
Remove-Item Env:DEEPSEEK_API_KEY

$env:SEMANTIC_SCHOLAR_API_KEY = "<key>"
scripts/add-secret-version.ps1 `
  -SecretId paper-harness-semantic-scholar-api-key `
  -ProjectId $Project `
  -ValueEnvironmentVariable SEMANTIC_SCHOLAR_API_KEY
Remove-Item Env:SEMANTIC_SCHOLAR_API_KEY
```

Inspect metadata only; never access or print secret values:

```powershell
gcloud secrets versions list paper-harness-database-url --project=$Project
gcloud secrets versions list paper-harness-deepseek-api-key --project=$Project
gcloud secrets versions list paper-harness-semantic-scholar-api-key --project=$Project
```

Use enabled positive numeric versions in the untracked `.tfvars` file.

## Build and publish images

Run the focused tests and static checks for the changed component before a
production build. Reserve the single canonical verification run for the final
milestone or release gate after production acceptance.

Choose a unique tag and build only the component that changed. Build and push
in one invocation when the registry is reachable:

```powershell
$Tag = "release-$(Get-Date -Format yyyyMMddHHmmss)"
scripts/build-images.ps1 `
  -ProjectId $Project `
  -Region $Region `
  -Tag $Tag `
  -Component daily `
  -Push
```

If that push alone fails after the image was built, retry the same tag without
rebuilding after network connectivity is restored:

```powershell
scripts/build-images.ps1 `
  -ProjectId $Project `
  -Region $Region `
  -Tag $Tag `
  -Component daily `
  -PushExisting
```

Resolve each component pushed in this release to an immutable Artifact Registry
digest and place its full `@sha256:...` reference in the untracked production
`.tfvars` file. For the Daily example above:

```powershell
gcloud artifacts docker images describe `
  "$Region-docker.pkg.dev/$Project/paper-harness/daily`:$Tag" `
  --project=$Project --format=json
```

Keep the current immutable digests for unchanged components. Do not deploy
mutable tags.

## Terraform plan and apply

Start from `infra/terraform/terraform.tfvars.example`. Keep the production copy
outside Git. Supply the existing project, owner, immutable image digests, and
enabled numeric secret versions.

Create a fresh plan:

```powershell
scripts/deploy.ps1 `
  -ProjectId $Project `
  -OwnerEmail $Owner `
  -TerraformStateBucket $StateBucket `
  -VarFile $VarFile `
  -Region $Region
```

Review every action. When the plan is authorized, run the same direct command
with `-Apply`:

```powershell
scripts/deploy.ps1 `
  -ProjectId $Project `
  -OwnerEmail $Owner `
  -TerraformStateBucket $StateBucket `
  -VarFile $VarFile `
  -Region $Region `
  -Apply
```

Use the safe deployment order:

1. Secret containers, Artifact Registry, APIs, and service accounts.
2. Migration Job.
3. Web/API and private GROBID.
4. Topic Daily Jobs with all fixed secret versions.
5. Topic Schedulers paused until the corresponding direct Daily verifications
   succeed.

## Migration

Confirm the migration Job uses the intended immutable Web/API digest and fixed
database secret version, then execute it once:

```powershell
scripts/run-production-migration.ps1 -ProjectId $Project -Region $Region
```

After completion, connect through an authorized database channel and run:

```sql
SELECT version_num FROM alembic_version;
```

The expected current revision is `0006_topic_reprocessing`.

## Private runtime verification

Inspect services, jobs, public IAM exposure, and the IAP allowlist:

```powershell
scripts/verify-private-runtime.ps1 `
  -ProjectId $Project `
  -OwnerEmail $Owner `
  -Region $Region
```

Also verify the owner can reach the Web/API through IAP, an unauthenticated
request is denied, readiness confirms migration head, and the Daily identity is
the only GROBID invoker.

## Direct Daily verification

Execute each deployed topic Job directly:

```powershell
scripts/run-production-daily.ps1 -ProjectId $Project -Region $Region
scripts/run-production-daily.ps1 -ProjectId $Project -Region $Region `
  -JobName paper-harness-daily-brain-computer-interfaces
scripts/run-production-daily.ps1 -ProjectId $Project -Region $Region `
  -JobName paper-harness-daily-world-models
```

To create a fresh same-date publication revision without changing the Job's
scheduled defaults, use per-execution environment overrides:

```powershell
scripts/run-production-daily.ps1 -ProjectId $Project -Region $Region `
  -JobName paper-harness-daily -LogicalDate 2026-08-22 -Reprocess
```

For BCI or World Models, replace `-JobName` with the corresponding exact name
shown above. A successful revision becomes the public result for that
topic/date; prior revisions remain available for audit.
The reprocess baseline is the widest terminal revision for that same topic and
date, so retrying failed analyses does not replace a full report with only the
previously failed remainder.

Verify all of the following from database and API reads:

- one terminal Daily pipeline execution exists;
- every selected paper with usable source metadata appears in the Daily report;
- source-analysis failures are marked `ANALYSIS_UNAVAILABLE` and optional
  enrichment gaps use their explicit neutral availability states;
- the report state matches item outcomes;
- report, graph, trend, and lineage records exist where the corpus supports
  them;
- evidence points to valid paper versions and claims;
- model, prompt, analysis scope, source, and verification provenance persist;
- the private Web/API returns the same persisted result; and
- logs contain no secrets, paper text, full prompts, or full model responses.

If the result is `PARTIAL`, confirm the report honestly lists each core
metadata/analysis failure and still includes its usable metadata card. Optional
enrichment failures do not block Scheduler or change a publication to
`PARTIAL`. If no relevant paper exists, confirm `COMPLETE / NO_UPDATE` for the
current logical date. `FAILED` is reserved for the documented system-level
fatal boundaries or an atomic publication transaction failure.

## Scheduler

Set `deploy_scheduler = true` and `scheduler_paused = true` only after direct
Daily verification succeeds for each configured topic. Apply the reviewed
Terraform plan and inspect the three paused jobs:

```powershell
scripts/verify-scheduler.ps1 -ProjectId $Project -Region $Region -Action Describe
scripts/verify-scheduler.ps1 -ProjectId $Project -Region $Region -SchedulerName paper-harness-daily-brain-computer-interfaces -Action Describe
scripts/verify-scheduler.ps1 -ProjectId $Project -Region $Region -SchedulerName paper-harness-daily-world-models -Action Describe
```

Set `scheduler_paused = false`, review the Terraform plan, and apply it to
enable the staggered `Asia/Kuala_Lumpur` schedules after the daily arXiv
announcement: Broad LLM Agents at 20:00, Brain-Computer Interfaces at 20:20,
and World Models at 20:40. Cloud Scheduler rejects manual invocations while a
job is paused, so run verification only after the Terraform-owned enablement:

```powershell
scripts/verify-scheduler.ps1 -ProjectId $Project -Region $Region -Action Run
scripts/verify-scheduler.ps1 -ProjectId $Project -Region $Region -SchedulerName paper-harness-daily-brain-computer-interfaces -Action Run
scripts/verify-scheduler.ps1 -ProjectId $Project -Region $Region -SchedulerName paper-harness-daily-world-models -Action Run
```

Confirm each forced invocation produced one expected topic-specific Daily
execution. To disable the schedules again, set `scheduler_paused = true` and
apply Terraform. Scheduler state remains owned by Terraform rather than an
imperative pause/resume helper.

## Routine checks

```powershell
gcloud run services describe paper-harness-web --project=$Project --region=$Region
gcloud run services describe paper-harness-grobid --project=$Project --region=$Region
gcloud run jobs describe paper-harness-daily --project=$Project --region=$Region
gcloud run jobs describe paper-harness-daily-brain-computer-interfaces --project=$Project --region=$Region
gcloud run jobs describe paper-harness-daily-world-models --project=$Project --region=$Region
gcloud run jobs executions list --job=paper-harness-daily --project=$Project --region=$Region
gcloud run jobs executions list --job=paper-harness-daily-brain-computer-interfaces --project=$Project --region=$Region
gcloud run jobs executions list --job=paper-harness-daily-world-models --project=$Project --region=$Region
gcloud scheduler jobs describe paper-harness-daily --project=$Project --location=$Region
gcloud scheduler jobs describe paper-harness-daily-brain-computer-interfaces --project=$Project --location=$Region
gcloud scheduler jobs describe paper-harness-daily-world-models --project=$Project --location=$Region
```

Operational logs should contain concise start, final result, publication, and
failure summaries. Record provider call counts, token counts when available,
durations, and cost estimates without recording sensitive content.

## Failure handling

- Global configuration, authentication, migration, database, and publication
  failures stop the run. Candidate schema or domain failures stay scoped to the
  narrowest identifiable item or provider operation and produce `PARTIAL` when
  at least one selected paper completes.
- Retry only the same transient operation for timeouts and HTTP
  429/500/502/503, within the centralized bounds.
- Never switch providers, parsers, databases, models, or analysis modes after a
  failure.
- A failed unpublished product attempt may discard its run-owned staging and
  replan from current valid persisted inputs. Published artifacts remain
  immutable.
- A publication transaction failure leaves the run failed and publishes
  nothing.
- For infrastructure failure, inspect the direct command output and current
  cloud state, correct the root cause, generate a fresh Terraform plan when
  infrastructure changed, and rerun only the failed direct operation.
