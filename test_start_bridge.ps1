[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$stage = Join-Path ([IO.Path]::GetTempPath()) ('xiaozhi-start-test-' + [guid]::NewGuid().ToString('N'))
try {
    $data = Join-Path $stage 'data'
    New-Item -ItemType Directory -Path $data -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $root 'start_bridge.ps1') -Destination $stage

    $python = (Get-Command python.exe -ErrorAction Stop).Source
    & $python -m venv (Join-Path $stage '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create test virtual environment.' }
    @{
        workspace = $stage
        bridge_host = '127.0.0.1'
        bridge_port = 18765
        default_agent = 'codex'
        codex_sandbox = 'workspace-write'
        workbuddy_permission = 'acceptEdits'
        codex_proxy = ''
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $data 'settings.json') -Encoding UTF8
    ConvertTo-SecureString 'wss://example.invalid/mcp?token=fixture' -AsPlainText -Force |
        ConvertFrom-SecureString | Set-Content -LiteralPath (Join-Path $data 'mcp-endpoint.dat') -Encoding ASCII
    @'
import os
print('BOOT_OK' if os.environ.get('XIAOZHI_MCP_ENDPOINT') == 'wss://example.invalid/mcp?token=fixture' else 'BAD_ENDPOINT', flush=True)
'@ | Set-Content -LiteralPath (Join-Path $stage 'xiaozhi_mcp.py') -Encoding ASCII

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $stage 'start_bridge.ps1')
    $output = Get-Content -Raw -LiteralPath (Join-Path $data 'bridge.out.log')
    if ($output.Trim() -ne 'BOOT_OK') { throw "Startup regression: $output" }
    Write-Host 'PASS: a stored endpoint with a trailing newline starts the bridge.'
} finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
