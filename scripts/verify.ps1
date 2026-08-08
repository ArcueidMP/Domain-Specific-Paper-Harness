[CmdletBinding()]
param(
    [int]$PostgresPort = 55432,
    [switch]$SkipImageBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepositoryRoot

function Resolve-RequiredCommand {
    param(
        [Parameter(Mandatory)]
        [string]$Name,
        [string[]]$FallbackPaths = @()
    )

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    foreach ($path in $FallbackPaths) {
        if (Test-Path -LiteralPath $path) {
            return $path
        }
    }

    throw "Required command '$Name' was not found."
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory)]
        [string]$Label,
        [Parameter(Mandatory)]
        [scriptblock]$Action
    )

    Write-Host "==> $Label"
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE."
    }
}

function Remove-DisposableComposeProject {
    param(
        [Parameter(Mandatory)]
        [string]$ProjectName
    )

    # Docker Compose emits a warning on stderr when there is nothing to remove.
    # PowerShell 5.1 promotes that harmless warning under Stop, so judge cleanup
    # by Docker's exit code while suppressing its expected no-op output.
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ExitCode = -1
    try {
        $ErrorActionPreference = "Continue"
        & $Docker compose -p $ProjectName down --volumes --remove-orphans *> $null
        $ExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    if ($ExitCode -ne 0) {
        throw "Disposable Docker Compose cleanup failed with exit code $ExitCode."
    }
}

$Uv = Resolve-RequiredCommand -Name "uv" -FallbackPaths @("D:\Tools\uv\uv.exe")
$Corepack = Resolve-RequiredCommand -Name "corepack"
$Docker = Resolve-RequiredCommand -Name "docker"
$Terraform = Resolve-RequiredCommand -Name "terraform" -FallbackPaths @("D:\Tools\terraform\terraform.exe")

$env:UV_PYTHON = "3.13.13"
$env:POSTGRES_DB = "paper_harness_verify"
$env:POSTGRES_USER = "paper_harness_verify"
$env:POSTGRES_PASSWORD = "paper_harness_verify_local"
$env:POSTGRES_PORT = $PostgresPort.ToString()
$env:DATABASE_URL = "postgresql+psycopg://paper_harness_verify:paper_harness_verify_local@localhost:$PostgresPort/paper_harness_verify"
$env:TEST_DATABASE_URL = $env:DATABASE_URL

Invoke-Checked "Frozen Python environment" {
    & $Uv sync --frozen --python 3.13.13
}

Write-Host "==> Exact Python runtime"
$PythonVersion = (& $Uv run --frozen --python 3.13.13 python -c "import platform; print(platform.python_version())").Trim()
if ($LASTEXITCODE -ne 0 -or $PythonVersion -ne "3.13.13") {
    throw "Expected CPython 3.13.13, resolved '$PythonVersion'."
}

Invoke-Checked "Ruff lint" {
    & $Uv run --frozen --python 3.13.13 ruff check .
}
Invoke-Checked "Ruff format check" {
    & $Uv run --frozen --python 3.13.13 ruff format --check .
}
Invoke-Checked "Pyright" {
    & $Uv run --frozen --python 3.13.13 pyright
}

$GeneratedOpenApi = New-TemporaryFile
try {
    Invoke-Checked "FastAPI OpenAPI contract" {
        & $Uv run --frozen --python 3.13.13 python -c `
            "from pathlib import Path; from paper_harness.entrypoints.openapi import generate_openapi; import sys; generate_openapi(Path(sys.argv[1]))" `
            $GeneratedOpenApi.FullName
    }
    $CheckedInOpenApi = Join-Path $RepositoryRoot "apps\api\openapi.json"
    if (-not (Test-Path -LiteralPath $CheckedInOpenApi)) {
        throw "The checked-in FastAPI OpenAPI contract is missing."
    }
    if ((Get-FileHash -LiteralPath $GeneratedOpenApi.FullName -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $CheckedInOpenApi -Algorithm SHA256).Hash) {
        throw "apps/api/openapi.json is stale. Regenerate it from FastAPI before verification."
    }
}
finally {
    Remove-Item -LiteralPath $GeneratedOpenApi -Force
}

Invoke-Checked "Frozen frontend environment" {
    & $Corepack pnpm install --frozen-lockfile
}
$GeneratedTypeContract = New-TemporaryFile
try {
    Invoke-Checked "Generated frontend API contract" {
        & $Corepack pnpm --filter "@paper-harness/web" exec openapi-typescript `
            "../api/openapi.json" --output $GeneratedTypeContract.FullName
    }
    $CheckedInTypeContract = Join-Path $RepositoryRoot "apps\web\src\api\schema.d.ts"
    if (-not (Test-Path -LiteralPath $CheckedInTypeContract)) {
        throw "The checked-in frontend API contract is missing."
    }
    if ((Get-FileHash -LiteralPath $GeneratedTypeContract.FullName -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $CheckedInTypeContract -Algorithm SHA256).Hash) {
        throw "apps/web/src/api/schema.d.ts is stale. Regenerate it from FastAPI OpenAPI before verification."
    }
}
finally {
    Remove-Item -LiteralPath $GeneratedTypeContract -Force
}
Invoke-Checked "Frontend lint" {
    & $Corepack pnpm lint
}
Invoke-Checked "Frontend typecheck" {
    & $Corepack pnpm typecheck
}
Invoke-Checked "Frontend unit tests" {
    & $Corepack pnpm test
}
Invoke-Checked "Frontend production build" {
    & $Corepack pnpm build
}
Invoke-Checked "Playwright Chromium runtime" {
    & $Corepack pnpm exec playwright install chromium
}
Invoke-Checked "Frontend browser smoke tests" {
    & $Corepack pnpm exec playwright test --config tests/e2e/playwright.config.ts
}

Invoke-Checked "Docker Compose configuration" {
    & $Docker compose config --quiet
}

Push-Location (Join-Path $RepositoryRoot "infra\terraform")
try {
    Invoke-Checked "Terraform format" {
        & $Terraform fmt -check -recursive
    }
    Invoke-Checked "Terraform initialization" {
        & $Terraform init -backend=false -input=false
    }
    Invoke-Checked "Terraform validation" {
        & $Terraform validate
    }
}
finally {
    Pop-Location
}

$VerifyComposeProject = "paper-harness-verify"
try {
    # This project name and volume are reserved for disposable verification data.
    Remove-DisposableComposeProject -ProjectName $VerifyComposeProject
    Invoke-Checked "Disposable PostgreSQL with pgvector" {
        & $Docker compose -p $VerifyComposeProject up --detach --wait db
    }
    Invoke-Checked "Clean Alembic upgrade" {
        & $Uv run --frozen --python 3.13.13 alembic upgrade head
    }
    Invoke-Checked "Alembic revision check" {
        & $Uv run --frozen --python 3.13.13 alembic check
    }
    Invoke-Checked "Alembic head state" {
        & $Uv run --frozen --python 3.13.13 alembic current --check-heads
    }
    Invoke-Checked "Python tests" {
        & $Uv run --frozen --python 3.13.13 pytest
    }
}
finally {
    Remove-DisposableComposeProject -ProjectName $VerifyComposeProject
}

if (-not $SkipImageBuild) {
    Invoke-Checked "Web/API image" {
        & $Docker build --file infra/docker/Dockerfile.api --tag paper-harness-api:verify .
    }
    Invoke-Checked "Daily Job image" {
        & $Docker build --file infra/docker/Dockerfile.daily --tag paper-harness-daily:verify .
    }
    Invoke-Checked "Pinned GROBID wrapper image" {
        & $Docker build --file infra/docker/Dockerfile.grobid --tag paper-harness-grobid:verify .
    }
}

Write-Host "All canonical verification checks passed."
