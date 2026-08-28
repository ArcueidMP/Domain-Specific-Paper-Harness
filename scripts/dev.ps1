[CmdletBinding()]
param(
    [int]$ApiPort = 8000,
    [int]$WebPort = 5173,
    [int]$PostgresPort = 5432
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepositoryRoot

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
$Uv = if ($null -ne $UvCommand) { $UvCommand.Source } else { "D:\Tools\uv\uv.exe" }
if (-not (Test-Path -LiteralPath $Uv) -and $null -eq $UvCommand) {
    throw "uv was not found on PATH or at D:\Tools\uv\uv.exe."
}

$Corepack = (Get-Command corepack -ErrorAction Stop).Source
$Docker = (Get-Command docker -ErrorAction Stop).Source

$env:POSTGRES_PORT = $PostgresPort.ToString()
if ([string]::IsNullOrWhiteSpace($env:DATABASE_URL)) {
    $env:DATABASE_URL = "postgresql+psycopg://paper_harness:paper_harness_local@localhost:$PostgresPort/paper_harness"
}
$env:API_PORT = $ApiPort.ToString()

& $Docker compose up --detach --wait db
if ($LASTEXITCODE -ne 0) { throw "PostgreSQL failed to start." }
& $Uv sync --frozen --python 3.13.13
if ($LASTEXITCODE -ne 0) { throw "Python dependency synchronization failed." }
& $Corepack pnpm install --frozen-lockfile
if ($LASTEXITCODE -ne 0) { throw "Frontend dependency synchronization failed." }
& $Uv run --frozen --python 3.13.13 alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }

$ApiArguments = @(
    "run", "--frozen", "--python", "3.13.13",
    "uvicorn", "paper_harness_api.main:app",
    "--host", "127.0.0.1", "--port", $ApiPort.ToString(), "--reload"
)
$WebArguments = @(
    "pnpm", "--filter", "@paper-harness/web", "dev",
    "--host", "127.0.0.1", "--port", $WebPort.ToString()
)

$ApiProcess = Start-Process -FilePath $Uv -ArgumentList $ApiArguments -NoNewWindow -PassThru
$WebProcess = Start-Process -FilePath $Corepack -ArgumentList $WebArguments -NoNewWindow -PassThru

Write-Host "API: http://127.0.0.1:$ApiPort"
Write-Host "Web: http://127.0.0.1:$WebPort"
Write-Host "Press Ctrl+C to stop the application processes; PostgreSQL remains available."

try {
    while (-not $ApiProcess.HasExited -and -not $WebProcess.HasExited) {
        Start-Sleep -Seconds 1
    }

    if ($ApiProcess.HasExited) {
        throw "The API process exited with code $($ApiProcess.ExitCode)."
    }
    throw "The web process exited with code $($WebProcess.ExitCode)."
}
finally {
    foreach ($process in @($ApiProcess, $WebProcess)) {
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id
        }
    }
}
