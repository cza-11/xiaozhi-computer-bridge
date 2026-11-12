[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'XiaozhiComputerBridge'),
    [string]$Workspace,
    [string]$BridgeHost = '127.0.0.1',
    [int]$BridgePort = 8765,
    [switch]$NoAutoStart
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# Locate a supported Python runtime without depending on a particular PATH.
# 检测可用的 Python 运行环境。
function Get-PythonCommand {
    $commands = @('py.exe', 'python.exe', 'python3.exe')
    foreach ($name in $commands) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -eq $command) { continue }
        try {
            if ($name -eq 'py.exe') {
                & $command.Source -3 -c "import sys; assert sys.version_info >= (3, 10)" 2>$null
                if ($LASTEXITCODE -eq 0) { return @($command.Source, '-3') }
            } else {
                & $command.Source -c "import sys; assert sys.version_info >= (3, 10)" 2>$null
                if ($LASTEXITCODE -eq 0) { return @($command.Source) }
            }
        } catch {}
    }
    return $null
}

# Install Python for the current user only when no supported runtime exists.
function Install-PythonIfNeeded {
    $python = Get-PythonCommand
    if ($null -ne $python) { return $python }
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($null -eq $winget) {
        throw '未找到 Python 3.10+，且系统没有 winget。请先安装 Python 3.12 后重试。'
    }
    Write-Host '未检测到 Python 3.10+，正在为当前用户安装 Python 3.12……'
    & $winget.Source install --id Python.Python.3.12 --exact --scope user --silent `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw 'Python安装失败。' }
    $candidate = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    if (-not (Test-Path -LiteralPath $candidate)) { throw 'Python已安装，但未找到python.exe。' }
    return @($candidate)
}

if ($BridgePort -lt 1 -or $BridgePort -gt 65535) { throw 'BridgePort必须在1到65535之间。' }
if ([string]::IsNullOrWhiteSpace($BridgeHost)) { throw 'BridgeHost不能为空。' }

Write-Host '=== 小智电脑桥一键安装 ==='
Write-Host "安装目录：$InstallRoot"

if (-not $Workspace) {
    $answer = Read-Host "请输入允许Agent工作的目录，直接回车使用 $HOME"
    $Workspace = if ([string]::IsNullOrWhiteSpace($answer)) { $HOME } else { $answer }
}
$Workspace = [IO.Path]::GetFullPath($Workspace)
if (-not (Test-Path -LiteralPath $Workspace -PathType Container)) {
    throw "工作目录不存在：$Workspace"
}

Write-Host '请在您自己的小智账号中选定智能体，并粘贴该智能体的完整 MCP 接入点。输入不会显示。'
# Keep the token in a SecureString while validating the endpoint shape.
# 隐藏用户输入并校验 MCP 地址格式。
$secureEndpoint = Read-Host 'MCP接入点' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureEndpoint)
try {
    $plainEndpoint = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim()
    $uri = $null
    if (-not [Uri]::TryCreate($plainEndpoint, [UriKind]::Absolute, [ref]$uri)) {
        throw 'MCP接入点不是有效URL。'
    }
    if ($uri.Scheme -notin @('wss', 'ws')) { throw 'MCP接入点必须使用wss://或ws://。' }
    if ([string]::IsNullOrWhiteSpace($uri.Host)) { throw 'MCP接入点缺少服务器地址。' }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

$pythonCommand = Install-PythonIfNeeded
# Expand the embedded, version-matched application payload into a temporary
# directory before copying it to the per-user installation directory.
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) ('xiaozhi-bridge-' + [guid]::NewGuid().ToString('N'))
$ZipPath = Join-Path $TempRoot 'payload.zip'
$ExtractPath = Join-Path $TempRoot 'payload'
New-Item -ItemType Directory -Path $TempRoot,$ExtractPath -Force | Out-Null
try {
    $payload = '__PAYLOAD_BASE64__'
    [IO.File]::WriteAllBytes($ZipPath, [Convert]::FromBase64String($payload))
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $ExtractPath -Force
    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    Copy-Item -Path (Join-Path $ExtractPath '*') -Destination $InstallRoot -Recurse -Force
} finally {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

$DataDir = Join-Path $InstallRoot 'data'
New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
$SecretPath = Join-Path $DataDir 'mcp-endpoint.dat'
# ConvertFrom-SecureString uses current-user DPAPI on Windows, so another user
# account cannot decrypt the stored endpoint token.
# 使用 Windows 当前用户的 DPAPI 保护接入点凭据。
$secureEndpoint | ConvertFrom-SecureString | Set-Content -LiteralPath $SecretPath -Encoding ASCII
Remove-Variable plainEndpoint -ErrorAction SilentlyContinue

$VenvPython = Join-Path $InstallRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    $pythonExe = $pythonCommand[0]
    $pythonPrefix = @()
    if ($pythonCommand.Count -gt 1) { $pythonPrefix = $pythonCommand[1..($pythonCommand.Count - 1)] }
    & $pythonExe @pythonPrefix -m venv (Join-Path $InstallRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '创建Python虚拟环境失败。' }
}
& $VenvPython -m pip install --disable-pip-version-check --quiet -r (Join-Path $InstallRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '安装Python依赖失败。' }

$env:PYTHONPATH = $InstallRoot
# Discover supported local agent launchers; prefer WorkBuddy as the fallback.
# 查找受支持的本机 Agent；默认回退优先选择 WorkBuddy。
$detection = & $VenvPython -c "from codex_runner import discover_codex; from workbuddy_runner import discover_workbuddy_command; from deepseek_runner import discover_deepseek; print(','.join(name for name, found in [('workbuddy', discover_workbuddy_command()), ('codex', discover_codex()), ('deepseek', discover_deepseek())] if found))"
$availableAgents = @($detection.Trim().Split(',') | Where-Object { $_ })
if ($availableAgents.Count -eq 0) {
    throw '没有检测到可用Agent。请至少安装并配置WorkBuddy、Codex或DeepSeek Harness后重新运行安装器。'
}
$defaultAgent = if ($availableAgents -contains 'workbuddy') { 'workbuddy' } elseif ($availableAgents -contains 'codex') { 'codex' } else { 'deepseek' }

$settings = [ordered]@{
    workspace = $Workspace
    bridge_host = $BridgeHost
    bridge_port = $BridgePort
    default_agent = $defaultAgent
    codex_sandbox = 'workspace-write'
    workbuddy_permission = 'acceptEdits'
    codex_proxy = ''
}
$settings | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $DataDir 'settings.json') -Encoding UTF8

if (-not $NoAutoStart) {
    # Register per-user startup without requiring administrator privileges.
    $startScript = Join-Path $InstallRoot 'start_bridge.ps1'
    $runValue = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $startScript + '" -Detached'
    New-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Force | Out-Null
    New-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
        -Name 'XiaozhiComputerBridge' -Value $runValue -PropertyType String -Force | Out-Null
}

$start = Join-Path $InstallRoot 'start_bridge.ps1'
& $start -Restart -Detached
Write-Host "安装完成。目录：$InstallRoot"
Write-Host "状态检查：powershell -ExecutionPolicy Bypass -File `"$start`" -Status"
