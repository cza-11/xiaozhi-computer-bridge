[CmdletBinding()]
param(
    [switch]$Detached,
    [switch]$Restart,
    [switch]$Stop,
    [switch]$Status,
    [switch]$SetEndpoint
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = Join-Path $Root 'data'
$SettingsPath = Join-Path $DataDir 'settings.json'
$SecretPath = Join-Path $DataDir 'mcp-endpoint.dat'
$PidPath = Join-Path $DataDir 'bridge.pid'
$StdoutPath = Join-Path $DataDir 'bridge.out.log'
$StderrPath = Join-Path $DataDir 'bridge.err.log'
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$EntryPoint = Join-Path $Root 'xiaozhi_mcp.py'

# Load only non-secret runtime settings. The MCP endpoint is stored separately.
# 加载非敏感设置；MCP 接入地址单独加密保存。
function Get-BridgeSettings {
    if (-not (Test-Path -LiteralPath $SettingsPath)) {
        throw "缺少配置文件：$SettingsPath。请重新运行 install.ps1。"
    }
    return Get-Content -Raw -Encoding UTF8 -LiteralPath $SettingsPath | ConvertFrom-Json
}

# Trust only a PID whose process command line points at this installation.
function Get-BridgeProcess {
    if (-not (Test-Path -LiteralPath $PidPath)) { return $null }
    $savedPid = 0
    if (-not [int]::TryParse((Get-Content -Raw -LiteralPath $PidPath).Trim(), [ref]$savedPid)) {
        return $null
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$savedPid" -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    if ($process.Name -ne 'python.exe' -or $process.CommandLine -notlike "*$EntryPoint*") {
        return $null
    }
    return $process
}

# Stop only the validated bridge process and remove its stale PID record.
function Stop-Bridge {
    $process = Get-BridgeProcess
    if ($null -ne $process) {
        Stop-Process -Id $process.ProcessId -Force
        Start-Sleep -Milliseconds 500
    }
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
}

if ($SetEndpoint) {
    if (-not (Test-Path -LiteralPath $Python)) { throw "缺少虚拟环境：$Python，请先安装电脑桥。" }
    Write-Host '请粘贴您自己的小智智能体 MCP 接入点，输入不会显示。'
    $newSecureEndpoint = Read-Host 'MCP接入点' -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($newSecureEndpoint)
    $previousEndpoint = $env:XIAOZHI_MCP_ENDPOINT
    try {
        $newEndpoint = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer).Trim()
        # Validate without placing credentials in the command line or output.
        # 通过环境变量验证接入点，避免在命令行中暴露凭据。
        $env:XIAOZHI_MCP_ENDPOINT = $newEndpoint
        & $Python -c "import os, sys; sys.path.insert(0, sys.argv[1]); from mcp_endpoint import validate_mcp_endpoint; validate_mcp_endpoint(os.environ.get('XIAOZHI_MCP_ENDPOINT', ''))" $Root
        if ($LASTEXITCODE -ne 0) { throw '接入点格式无效，原有配置未更改。' }
        $newSecureEndpoint = ConvertTo-SecureString -String $newEndpoint -AsPlainText -Force
        New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
        $newSecureEndpoint | ConvertFrom-SecureString | Set-Content -LiteralPath $SecretPath -Encoding ASCII -NoNewline
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        $env:XIAOZHI_MCP_ENDPOINT = $previousEndpoint
        Remove-Variable newEndpoint -ErrorAction SilentlyContinue
    }
    Write-Host '接入点已保存。请在当前任务结束后运行 -Restart -Detached 连接新智能体。'
    exit 0
}

if ($Stop) {
    Stop-Bridge
    Write-Host '小智电脑桥已停止。'
    exit 0
}

$settings = Get-BridgeSettings
$HealthUrl = "http://$($settings.bridge_host):$($settings.bridge_port)/health"

if ($Status) {
    try {
        $health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
        $health | ConvertTo-Json -Depth 5
        exit 0
    } catch {
        Write-Host '小智电脑桥未运行或本机健康接口不可达。'
        exit 1
    }
}

if ($Restart) { Stop-Bridge }

$running = Get-BridgeProcess
if ($null -ne $running) {
    try {
        $health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
        if ($health.mcp_status -eq 'connected') {
            Write-Host "小智电脑桥已经连接，进程号：$($running.ProcessId)"
            exit 0
        }
    } catch {}
    throw '检测到旧桥进程仍在运行但未连接。请执行 start_bridge.ps1 -Restart -Detached。'
}

if ($Detached) {
    # Start a hidden controller and wait until its local health endpoint reports
    # that the outbound MCP WebSocket is connected.
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', ('"' + $MyInvocation.MyCommand.Path + '"')
    )
    $ControllerStdoutPath = Join-Path $DataDir 'controller.out.log'
    $ControllerStderrPath = Join-Path $DataDir 'controller.err.log'
    $controller = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $ControllerStdoutPath -RedirectStandardError $ControllerStderrPath

    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $lastStatus = 'starting'
    do {
        Start-Sleep -Milliseconds 500
        $controller.Refresh()
        if ($controller.HasExited) {
            throw "桥接程序启动失败（退出码 $($controller.ExitCode)）。请查看：$ControllerStderrPath"
        }
        try {
            $health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 1
            $lastStatus = [string]$health.mcp_status
            if ($lastStatus -eq 'connected') {
                $agents = ($health.available_agents -join ', ')
                Write-Host "部署成功：小智 MCP 已连接。可用执行器：$agents；默认：$($health.default_agent)。"
                exit 0
            }
            if ($lastStatus -eq 'auth_failed') {
                throw "小智 MCP 认证失败（HTTP $($health.mcp_http_status)），请重新运行安装器并输入新接入点。"
            }
        } catch {
            if ($_.Exception.Message -like '*认证失败*') { throw }
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "30秒内未连接小智，当前状态：$lastStatus。请查看：$ControllerStderrPath、$StderrPath、$StdoutPath"
}

if (-not (Test-Path -LiteralPath $Python)) { throw "缺少虚拟环境：$Python" }
if (-not (Test-Path -LiteralPath $SecretPath)) { throw "缺少加密接入点：$SecretPath" }

# Decrypt the current-user DPAPI secret only long enough to place it in the
# child process environment; never put the token in command-line arguments.
# 使用当前用户的 DPAPI 解密地址，并通过环境变量传给子进程。
$secureEndpoint = ConvertTo-SecureString -String (Get-Content -Raw -LiteralPath $SecretPath).Trim()
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureEndpoint)
try {
    $endpoint = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

$env:XIAOZHI_MCP_ENDPOINT = $endpoint
$env:XIAOZHI_WORKSPACE = [string]$settings.workspace
$env:XIAOZHI_BRIDGE_HOST = [string]$settings.bridge_host
$env:XIAOZHI_BRIDGE_PORT = [string]$settings.bridge_port
$env:XIAOZHI_BRIDGE_DATA_DIR = $DataDir
$env:XIAOZHI_BRIDGE_DB = Join-Path $DataDir 'tasks.sqlite3'
$env:XIAOZHI_DEFAULT_AGENT = [string]$settings.default_agent
$env:CODEX_BRIDGE_PROXY = [string]$settings.codex_proxy
$env:PYTHONUTF8 = '1'
Remove-Variable endpoint

# The foreground controller owns the child lifetime, logs, and PID record.
$arguments = @(
    ('"' + $EntryPoint + '"'),
    '--mode', 'live',
    '--sandbox', [string]$settings.codex_sandbox,
    '--workbuddy-permission', [string]$settings.workbuddy_permission
)
$process = Start-Process -FilePath $Python -ArgumentList $arguments -PassThru -NoNewWindow `
    -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath
Set-Content -LiteralPath $PidPath -Value $process.Id -Encoding ASCII
try {
    $process.WaitForExit()
    exit $process.ExitCode
} finally {
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
}
