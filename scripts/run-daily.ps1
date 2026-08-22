[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgumentList = @()
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

$Arguments = @(
    "run", "--frozen", "--python", "3.13.13",
    "paper-harness-daily"
)
$Arguments += $ArgumentList

& $Uv @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "Daily CLI failed with exit code $LASTEXITCODE."
}
