# Deploys supabase/functions/embed without Docker (--use-api) and with JWT
# verification off (the function checks EMBED_TOKEN itself). Writes EMBED_URL
# to the root .env (not a secret). Prints the project ref and the URL only.
#
#   powershell -File scripts\deploy-embed.ps1

. (Join-Path $PSScriptRoot 'supabase-env.ps1')

$ref = Get-ProjectRef
$code = Invoke-SupabaseCli @('functions', 'deploy', 'embed', '--project-ref', $ref, '--no-verify-jwt', '--use-api')
if ($code -ne 0) {
    Write-Host "deploy failed (exit $code)"
    exit $code
}

$url = "https://$ref.supabase.co/functions/v1/embed"
Set-DotEnvValue $EnvFile 'EMBED_URL' $url
Write-Host "project ref: $ref"
Write-Host "function URL: $url"
