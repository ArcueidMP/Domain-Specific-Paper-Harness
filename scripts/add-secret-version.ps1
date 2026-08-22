[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern("^[a-z][a-z0-9-]{0,254}$")]
    [string]$SecretId,
    [Parameter(Mandatory)]
    [ValidatePattern("^[a-z][a-z0-9-]{4,28}[a-z0-9]$")]
    [string]$ProjectId,
    [ValidateSet("DATABASE_URL", "DEEPSEEK_API_KEY", "SEMANTIC_SCHOLAR_API_KEY")]
    [string]$ValueEnvironmentVariable
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-SecretValueBinding {
    param(
        [Parameter(Mandatory)]
        [string]$Id,
        [Parameter(Mandatory)]
        [string]$EnvironmentVariable
    )

    $ExpectedEnvironmentVariable = switch ($Id) {
        "paper-harness-database-url" { "DATABASE_URL" }
        "paper-harness-deepseek-api-key" { "DEEPSEEK_API_KEY" }
        "paper-harness-semantic-scholar-api-key" { "SEMANTIC_SCHOLAR_API_KEY" }
        default { throw "Secret container '$Id' is not supported by this injection script." }
    }
    if (-not [string]::IsNullOrWhiteSpace($EnvironmentVariable) -and
        $EnvironmentVariable -cne $ExpectedEnvironmentVariable) {
        throw "Secret container '$Id' must receive only '$ExpectedEnvironmentVariable'."
    }
    return $ExpectedEnvironmentVariable
}

function Assert-NormalizedProductionDatabaseUrl {
    param(
        [Parameter(Mandatory)]
        [string]$Value
    )

    if ($Value -cne $Value.Trim() -or $Value -cnotmatch "^postgresql\+psycopg://") {
        throw "DATABASE_URL is not a normalized production PostgreSQL URL."
    }

    $ParsedUrl = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$ParsedUrl) -or
        $null -eq $ParsedUrl -or $ParsedUrl.Scheme -cne "postgresql+psycopg") {
        throw "DATABASE_URL is not a normalized production PostgreSQL URL."
    }

    $UserInfo = $ParsedUrl.UserInfo -split ":", 2
    $DatabaseName = $ParsedUrl.AbsolutePath.TrimStart("/")
    if ([string]::IsNullOrWhiteSpace($ParsedUrl.Host) -or $UserInfo.Count -ne 2 -or
        [string]::IsNullOrWhiteSpace($UserInfo[0]) -or
        [string]::IsNullOrWhiteSpace($UserInfo[1]) -or
        [string]::IsNullOrWhiteSpace($DatabaseName)) {
        throw "DATABASE_URL is missing required production connection fields."
    }

    $QueryValues = @{}
    foreach ($Pair in $ParsedUrl.Query.TrimStart("?") -split "&") {
        if ([string]::IsNullOrEmpty($Pair)) {
            continue
        }
        $Components = $Pair -split "=", 2
        $Name = [Uri]::UnescapeDataString($Components[0]).ToLowerInvariant()
        if ($QueryValues.ContainsKey($Name)) {
            throw "DATABASE_URL contains duplicate query parameters."
        }
        $QueryValues[$Name] = if ($Components.Count -eq 2) {
            [Uri]::UnescapeDataString($Components[1]).ToLowerInvariant()
        }
        else {
            ""
        }
    }

    $TlsModes = @("require", "verify-ca", "verify-full")
    if (-not $QueryValues.ContainsKey("sslmode") -or
        $QueryValues["sslmode"] -cnotin $TlsModes) {
        throw "DATABASE_URL must require TLS."
    }
    $TruthyValues = @("1", "on", "true", "yes")
    if ($ParsedUrl.Port -eq 6543 -or $ParsedUrl.Host.ToLowerInvariant().Contains("-pooler.") -or
        ($QueryValues.ContainsKey("pool_mode") -and
            $QueryValues["pool_mode"] -ceq "transaction") -or
        ($QueryValues.ContainsKey("pgbouncer") -and
            $QueryValues["pgbouncer"] -cin $TruthyValues)) {
        throw "DATABASE_URL must use a direct or session-affine endpoint."
    }
}

$SecretPurpose = Assert-SecretValueBinding `
    -Id $SecretId `
    -EnvironmentVariable $ValueEnvironmentVariable

$Gcloud = (Get-Command gcloud -ErrorAction Stop).Source
$GcloudCmd = (Get-Command gcloud.cmd -ErrorAction Stop).Source

$SecureValue = $null
$PlainValue = $null
$PlainBytes = $null
$StandardOutput = $null
$StandardError = $null
$Process = $null
$Bstr = [IntPtr]::Zero
$OriginalConsoleInputEncoding = $null
try {
    if ([string]::IsNullOrWhiteSpace($ValueEnvironmentVariable)) {
        $SecureValue = Read-Host "Enter the $SecretPurpose value for '$SecretId'" -AsSecureString
        $Bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
        $PlainValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Bstr)
    }
    else {
        $PlainValue = [Environment]::GetEnvironmentVariable(
            $ValueEnvironmentVariable,
            [EnvironmentVariableTarget]::Process
        )
    }
    if ([string]::IsNullOrWhiteSpace($PlainValue)) {
        throw "The selected secret value is empty."
    }
    if ($PlainValue -cne $PlainValue.Trim() -or
        $PlainValue.IndexOfAny([char[]]@([char]0, [char]10, [char]13)) -ge 0) {
        throw "The selected secret value contains prohibited whitespace or control characters."
    }
    if ($SecretPurpose -ceq "DATABASE_URL") {
        Assert-NormalizedProductionDatabaseUrl -Value $PlainValue
    }

    & $Gcloud secrets describe $SecretId --project=$ProjectId --format="value(name)" --quiet *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Secret container '$SecretId' does not exist in project '$ProjectId'."
    }

    $Command = '"{0}" secrets versions add "{1}" --project="{2}" --data-file=- --format="value(name)" --quiet' -f `
        $GcloudCmd, $SecretId, $ProjectId
    $StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $env:ComSpec
    $StartInfo.Arguments = "/d /s /c `"$Command`""
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $StartInfo.RedirectStandardInput = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true

    $Process = [Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    # Process.Start captures Console.InputEncoding for its redirected stdin
    # writer on Windows PowerShell 5, so set the no-preamble encoding first.
    $OriginalConsoleInputEncoding = [Console]::InputEncoding
    [Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
    if (-not $Process.Start()) {
        throw "gcloud could not be started."
    }
    # Windows PowerShell 5 builds Process.StandardInput with Console.InputEncoding.
    # Its default UTF-8 encoding emits EF-BB-BF, which gcloud would persist as
    # part of the secret. Create the redirected writer while the console uses a
    # no-preamble UTF-8 encoding, then write and clear the exact bytes ourselves.
    try {
        $PlainBytes = [Text.UTF8Encoding]::new($false).GetBytes($PlainValue)
        $StandardInputStream = $Process.StandardInput.BaseStream
        $StandardInputStream.Write($PlainBytes, 0, $PlainBytes.Length)
        $StandardInputStream.Flush()
        $StandardInputStream.Close()
    }
    finally {
        [Console]::InputEncoding = $OriginalConsoleInputEncoding
        $OriginalConsoleInputEncoding = $null
    }
    $StandardOutput = $Process.StandardOutput.ReadToEnd()
    $StandardError = $Process.StandardError.ReadToEnd()
    $Process.WaitForExit()
    if ($Process.ExitCode -ne 0) {
        throw "gcloud failed to add an enabled version to '$SecretId'."
    }

    $VersionResource = $StandardOutput.Trim()
    $Version = ($VersionResource -split "/")[-1]
    if ($Version -notmatch "^[1-9][0-9]*$") {
        throw "Secret Manager returned an invalid version identifier."
    }

    # The numeric identifier is intentionally the only successful output.
    Write-Output $Version
}
finally {
    if ($null -ne $OriginalConsoleInputEncoding) {
        [Console]::InputEncoding = $OriginalConsoleInputEncoding
        $OriginalConsoleInputEncoding = $null
    }
    $PlainValue = $null
    if ($null -ne $PlainBytes) {
        [Array]::Clear($PlainBytes, 0, $PlainBytes.Length)
        $PlainBytes = $null
    }
    $StandardOutput = $null
    $StandardError = $null
    if ($Bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Bstr)
    }
    if ($null -ne $Process) {
        $Process.Dispose()
    }
}
