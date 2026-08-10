[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ProjectId,
    [Parameter(Mandatory)]
    [string]$OwnerEmail,
    [string]$Region = "asia-southeast1",
    [string]$ArtifactRepository = "paper-harness",
    [string]$DatabaseSecretId = "paper-harness-database-url",
    [string]$SemanticScholarSecretId = "paper-harness-semantic-scholar-api-key",
    [string]$ImageTag,
    [switch]$AttachSemanticScholarSecretToDaily,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$TerraformDirectory = Join-Path $RepositoryRoot "infra\terraform"
Set-Location $RepositoryRoot

if ([string]::IsNullOrWhiteSpace($ImageTag)) {
    $Revision = (git rev-parse --short=12 HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($Revision)) {
        throw "ImageTag was not supplied and the Git revision could not be resolved."
    }
    $ImageTag = "$Revision-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff'))"
}

$Gcloud = (Get-Command gcloud -ErrorAction Stop).Source
$Docker = (Get-Command docker -ErrorAction Stop).Source
$TerraformCommand = Get-Command terraform -ErrorAction SilentlyContinue
$Terraform = if ($null -ne $TerraformCommand) { $TerraformCommand.Source } else { "D:\Tools\terraform\terraform.exe" }
if (-not (Test-Path -LiteralPath $Terraform) -and $null -eq $TerraformCommand) {
    throw "Terraform was not found on PATH or at D:\Tools\terraform\terraform.exe."
}

function Assert-NoTerraformDeletes {
    param(
        [Parameter(Mandatory)]
        [string]$PlanPath,
        [Parameter(Mandatory)]
        [string]$Label
    )

    $PlanJsonText = (& $Terraform show -json $PlanPath) -join [Environment]::NewLine
    if ($LASTEXITCODE -ne 0) {
        throw "$Label could not be inspected for destructive actions."
    }
    $Plan = $PlanJsonText | ConvertFrom-Json
    $Deletes = @(
        $Plan.resource_changes |
            Where-Object { $_.change.actions -contains "delete" } |
            ForEach-Object { $_.address }
    )
    if ($Deletes.Count -gt 0) {
        throw "$Label contains delete or replacement actions: $($Deletes -join ', ')."
    }
}

$ActiveAccount = (& $Gcloud auth list --filter=status:ACTIVE "--format=value(account)").Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($ActiveAccount)) {
    throw "gcloud has no active authenticated account. Run 'gcloud auth login'."
}

$PreviousGoogleOAuthAccessToken = [Environment]::GetEnvironmentVariable(
    "GOOGLE_OAUTH_ACCESS_TOKEN",
    [EnvironmentVariableTarget]::Process
)

try {
    $AccessToken = (& $Gcloud auth print-access-token --project=$ProjectId).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($AccessToken)) {
        throw "gcloud could not issue an access token. Run 'gcloud auth login' or configure Application Default Credentials with 'gcloud auth application-default login'."
    }
    # Process-only provider authentication; the token is never written to a file or command output.
    $env:GOOGLE_OAUTH_ACCESS_TOKEN = $AccessToken

Push-Location $TerraformDirectory
try {
    & $Terraform init -input=false
    if ($LASTEXITCODE -ne 0) { throw "Terraform initialization failed." }

    # Keep targets before variables and quote the output option explicitly;
    # Windows PowerShell 5.1 otherwise turns native Terraform options into
    # positional input in some combinations.
    & $Terraform plan `
        -input=false `
        "-out=foundation.tfplan" `
        "-target=google_project_service.required" `
        "-target=google_artifact_registry_repository.containers" `
        "-target=google_secret_manager_secret.database_url" `
        "-target=google_secret_manager_secret.deepseek_api_key" `
        "-target=google_secret_manager_secret.semantic_scholar_api_key" `
        "-target=google_service_account.web" `
        "-target=google_service_account.daily" `
        "-target=google_service_account.scheduler" `
        "-target=google_project_iam_member.web_log_writer" `
        "-target=google_project_iam_member.daily_log_writer" `
        "-target=google_secret_manager_secret_iam_member.web_database" `
        "-target=google_secret_manager_secret_iam_member.daily_database" `
        "-var=project_id=$ProjectId" `
        "-var=owner_email=$OwnerEmail" `
        "-var=region=$Region" `
        "-var=artifact_repository_id=$ArtifactRepository" `
        "-var=database_secret_id=$DatabaseSecretId" `
        "-var=semantic_scholar_secret_id=$SemanticScholarSecretId" `
        "-var=deploy_runtime_resources=false"
    if ($LASTEXITCODE -ne 0) { throw "Terraform foundation plan failed." }
    Assert-NoTerraformDeletes -PlanPath "foundation.tfplan" -Label "Terraform foundation plan"
    if (-not $Apply) {
        Write-Host "Non-destructive foundation plan created. Re-run with -Apply to create foundation resources, publish images, and plan runtime deployment."
        return
    }

    & $Terraform apply -input=false -auto-approve foundation.tfplan
    if ($LASTEXITCODE -ne 0) { throw "Terraform foundation apply failed." }
}
finally {
    Pop-Location
}

$DatabaseVersionResource = (& $Gcloud secrets versions list $DatabaseSecretId --project=$ProjectId --filter='state=ENABLED' --sort-by='~createTime' --limit=1 "--format=value(name)").Trim()
if ([string]::IsNullOrWhiteSpace($DatabaseVersionResource)) {
    throw "Secret '$DatabaseSecretId' has no enabled version. Add the production DATABASE_URL as a Secret Manager version, then rerun this script."
}
$DatabaseVersion = ($DatabaseVersionResource -split '/')[-1]
if ($DatabaseVersion -notmatch '^\d+$') {
    throw "Secret Manager returned an invalid DATABASE_URL version name."
}

$SemanticScholarVersion = ""
if ($AttachSemanticScholarSecretToDaily) {
    $SemanticScholarVersionResource = (& $Gcloud secrets versions list $SemanticScholarSecretId --project=$ProjectId --filter='state=ENABLED' --sort-by='~createTime' --limit=1 "--format=value(name)").Trim()
    if ([string]::IsNullOrWhiteSpace($SemanticScholarVersionResource)) {
        throw "Secret '$SemanticScholarSecretId' has no enabled version. Add the Semantic Scholar API key as a Secret Manager version, then rerun this script."
    }
    $SemanticScholarVersion = ($SemanticScholarVersionResource -split '/')[-1]
    if ($SemanticScholarVersion -notmatch '^\d+$') {
        throw "Secret Manager returned an invalid Semantic Scholar API key version name."
    }
}
$AttachSemanticScholarSecret = $AttachSemanticScholarSecretToDaily.IsPresent.ToString().ToLowerInvariant()

& $Gcloud beta services identity create --service=iap.googleapis.com --project=$ProjectId --quiet
if ($LASTEXITCODE -ne 0) {
    throw "The IAP service agent could not be provisioned."
}

& $Gcloud auth configure-docker "$Region-docker.pkg.dev" --quiet
if ($LASTEXITCODE -ne 0) { throw "Artifact Registry Docker authentication failed." }

$Registry = "$Region-docker.pkg.dev/$ProjectId/$ArtifactRepository"
$WebTag = "$Registry/web-api:$ImageTag"
$DailyTag = "$Registry/daily:$ImageTag"

& $Docker build --file infra/docker/Dockerfile.api --tag $WebTag .
if ($LASTEXITCODE -ne 0) { throw "Web/API image build failed." }
& $Docker push $WebTag
if ($LASTEXITCODE -ne 0) { throw "Web/API image push failed." }
& $Docker build `
    --file infra/docker/Dockerfile.daily `
    --target production `
    --build-arg "PREPARE_SPECTER2_BASE=1" `
    --tag $DailyTag `
    .
if ($LASTEXITCODE -ne 0) { throw "Daily image build failed." }
& $Docker push $DailyTag
if ($LASTEXITCODE -ne 0) { throw "Daily image push failed." }

$WebDigest = (& $Gcloud artifacts docker images describe $WebTag --project=$ProjectId "--format=value(image_summary.digest)").Trim()
$DailyDigest = (& $Gcloud artifacts docker images describe $DailyTag --project=$ProjectId "--format=value(image_summary.digest)").Trim()
if ($WebDigest -notmatch '^sha256:[0-9a-f]{64}$' -or $DailyDigest -notmatch '^sha256:[0-9a-f]{64}$') {
    throw "Artifact Registry did not return immutable image digests."
}

$AccessToken = (& $Gcloud auth print-access-token --project=$ProjectId).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($AccessToken)) {
    throw "gcloud could not refresh the Terraform access token."
}
$env:GOOGLE_OAUTH_ACCESS_TOKEN = $AccessToken

Push-Location $TerraformDirectory
try {
    & $Terraform plan `
        -input=false `
        "-out=runtime.tfplan" `
        "-var=project_id=$ProjectId" `
        "-var=owner_email=$OwnerEmail" `
        "-var=region=$Region" `
        "-var=artifact_repository_id=$ArtifactRepository" `
        "-var=database_secret_id=$DatabaseSecretId" `
        "-var=semantic_scholar_secret_id=$SemanticScholarSecretId" `
        "-var=deploy_runtime_resources=true" `
        "-var=attach_semantic_scholar_secret_to_daily=$AttachSemanticScholarSecret" `
        "-var=web_api_image=$Registry/web-api@$WebDigest" `
        "-var=daily_image=$Registry/daily@$DailyDigest" `
        "-var=database_secret_version=$DatabaseVersion" `
        "-var=semantic_scholar_secret_version=$SemanticScholarVersion"
    if ($LASTEXITCODE -ne 0) { throw "Terraform runtime plan failed." }
    Assert-NoTerraformDeletes -PlanPath "runtime.tfplan" -Label "Terraform runtime plan"
    & $Terraform apply -input=false -auto-approve runtime.tfplan
    if ($LASTEXITCODE -ne 0) { throw "Terraform runtime apply failed." }
    & $Terraform output
}
finally {
    Pop-Location
}
}
finally {
    if ($null -eq $PreviousGoogleOAuthAccessToken) {
        Remove-Item Env:GOOGLE_OAUTH_ACCESS_TOKEN -ErrorAction SilentlyContinue
    }
    else {
        $env:GOOGLE_OAUTH_ACCESS_TOKEN = $PreviousGoogleOAuthAccessToken
    }
}
