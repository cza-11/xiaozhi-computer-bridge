[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Template = Join-Path $Root 'install-template.ps1'
$Output = Join-Path $Root 'install.ps1'
$Stage = Join-Path ([IO.Path]::GetTempPath()) ('xiaozhi-installer-' + [guid]::NewGuid().ToString('N'))
$Zip = Join-Path $Stage 'payload.zip'
$PayloadDir = Join-Path $Stage 'payload'
$Files = @(
    'bridge.py',
    'agent_router.py',
    'xiaozhi_adapter.py',
    'xiaozhi_mcp.py',
    'mcp_endpoint.py',
    'runtime_config.py',
    'codex_runner.py',
    'deepseek_runner.py',
    'workbuddy_runner.py',
    'start_bridge.ps1',
    'requirements.txt',
    'README.md'
)

# Package the exact runtime files and embed their ZIP payload into the template
# so customers can install from a single PowerShell script.
# 将运行文件打包并嵌入模板，生成单文件安装脚本。
New-Item -ItemType Directory -Path $PayloadDir -Force | Out-Null
try {
    foreach ($file in $Files) {
        Copy-Item -LiteralPath (Join-Path $Root $file) -Destination $PayloadDir
    }
    Compress-Archive -Path (Join-Path $PayloadDir '*') -DestinationPath $Zip -CompressionLevel Optimal
    $base64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($Zip))
    $content = Get-Content -Raw -Encoding UTF8 -LiteralPath $Template
    if (-not $content.Contains('__PAYLOAD_BASE64__')) { throw '安装模板缺少payload占位符。' }
    $content = $content.Replace('__PAYLOAD_BASE64__', $base64)
    Set-Content -LiteralPath $Output -Value $content -Encoding UTF8
    Write-Host "已生成：$Output"
} finally {
    Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
}
