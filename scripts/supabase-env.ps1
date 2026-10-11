# Shared by gen-embed-token.ps1 and deploy-embed.ps1. Dot-source it.
#
# Never prints a value. The access token is placed in this process's
# environment only for the duration of one CLI call (Invoke-SupabaseCli), and
# every line the CLI prints passes through Hide-Secrets first.

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $RepoRoot '.env'
$WebEnvFile = Join-Path $RepoRoot 'apps\web\.env.local'

function Get-DotEnvValue([string]$Path, [string]$Key) {
    if (-not (Test-Path $Path)) { return $null }
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=(.*)$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

# Replace KEY=... in place, or append it. UTF-8 without BOM, other lines untouched.
function Set-DotEnvValue([string]$Path, [string]$Key, [string]$Value) {
    $lines = New-Object System.Collections.Generic.List[string]
    if (Test-Path $Path) { $lines.AddRange([string[]][IO.File]::ReadAllLines($Path)) }
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*$([regex]::Escape($Key))\s*=") { $lines[$i] = "$Key=$Value"; $found = $true }
    }
    if (-not $found) { $lines.Add("$Key=$Value") }
    [IO.File]::WriteAllLines($Path, $lines, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-ProjectRef {
    $url = Get-DotEnvValue $WebEnvFile 'NEXT_PUBLIC_SUPABASE_URL'
    if (-not $url) { throw 'NEXT_PUBLIC_SUPABASE_URL is missing from apps/web/.env.local' }
    return ([uri]$url).Host.Split('.')[0]
}

$script:Secrets = @()

function Hide-Secrets([string]$Text) {
    foreach ($s in $script:Secrets) { if ($s) { $Text = $Text.Replace($s, '[redacted]') } }
    return $Text
}

# Runs `npx supabase@latest <args>` from the repo root with the access token
# in the environment for this call only. Returns the exit code; prints the
# CLI's output redacted.
function Invoke-SupabaseCli([string[]]$CliArgs) {
    $token = Get-DotEnvValue $EnvFile 'SUPABASE_ACCESS_TOKEN'
    if (-not $token) { throw 'SUPABASE_ACCESS_TOKEN is missing from the root .env' }
    $script:Secrets += $token
    Push-Location $RepoRoot
    $env:SUPABASE_ACCESS_TOKEN = $token
    # 5.1 wraps each stderr line of a native command in an ErrorRecord; under
    # 'Stop' the first progress line would abort the call.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & npx --yes supabase@latest @CliArgs 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
        Remove-Item Env:\SUPABASE_ACCESS_TOKEN -ErrorAction SilentlyContinue
        Pop-Location
    }
    foreach ($line in $out) { Write-Host (Hide-Secrets ([string]$line)) }
    return $code
}
