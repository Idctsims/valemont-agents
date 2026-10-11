# Generates EMBED_TOKEN (32 random bytes, base64url), writes it to the root
# .env and sets it as the embed function's secret. Never prints it.
#
#   powershell -File scripts\gen-embed-token.ps1          (refuses to rotate)
#   powershell -File scripts\gen-embed-token.ps1 -Force   (rotates)
#
# Rotating breaks the worker until EMBED_TOKEN is updated on Railway too.
# The secret reaches the CLI through a temp env-file that is deleted in a
# finally block, so the value never appears on a command line.

param([switch]$Force)

. (Join-Path $PSScriptRoot 'supabase-env.ps1')

if ((Get-DotEnvValue $EnvFile 'EMBED_TOKEN') -and -not $Force) {
    Write-Host 'EMBED_TOKEN already exists in .env. Rotating breaks the worker until Railway is updated; pass -Force to rotate.'
    exit 1
}

$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
$token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
$script:Secrets += $token

$ref = Get-ProjectRef
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("embed-secret-" + [guid]::NewGuid().ToString('N') + '.env')
try {
    [IO.File]::WriteAllText($tmp, "EMBED_TOKEN=$token`n", (New-Object System.Text.UTF8Encoding($false)))
    $code = Invoke-SupabaseCli @('secrets', 'set', '--env-file', $tmp, '--project-ref', $ref)
} finally {
    if (Test-Path $tmp) { Remove-Item $tmp -Force }
}
if ($code -ne 0) {
    Write-Host "supabase secrets set failed (exit $code). .env was NOT changed."
    exit $code
}

Set-DotEnvValue $EnvFile 'EMBED_TOKEN' $token
Write-Host "EMBED_TOKEN ($($token.Length) chars) written to .env and set as a function secret on project $ref. Temp file deleted: $(-not (Test-Path $tmp))."
