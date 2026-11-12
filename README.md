# 小智电脑任务桥

通过小智智能体向 Windows 电脑上的 WorkBuddy、Codex 或 DeepSeek Harness 发送任务。三个执行器共用单任务队列；任务接收后返回短编号，并可查询当前任务和结果。

## 安装前准备

1. 在小智平台或兼容的自建平台创建智能体，将实体设备绑定到该智能体，并复制该智能体的 MCP WebSocket 接入点。设备绑定的智能体与 MCP 接入点所属智能体必须一致。
2. 在 Windows 电脑上安装并配置至少一个受支持的 Agent：WorkBuddy、Codex 或 DeepSeek Harness。检测到程序不代表登录和模型配置已完成，请先在 Agent 中测试。
3. 确保电脑可以联网。缺少 Python 3.10 及以上版本时，安装器会尝试使用 Windows `winget` 安装 Python 3.12，并安装项目依赖。

## 一条命令安装

在 Windows PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/cza-11/xiaozhi-computer-bridge/main/install.ps1 | iex"
```

安装过程中：

1. 输入允许 Agent 工作的目录；直接回车使用当前用户目录。Agent 根据自身权限设置可能执行文件和命令操作，请选择适当目录。
2. 粘贴自己的完整 MCP 接入点并按回车。输入不会显示；地址中的令牌需要保密。
3. 等待安装器检测 Agent、安装依赖并连接。默认安装到 `%LOCALAPPDATA%\XiaozhiComputerBridge`，并设置当前用户登录时启动。
4. 在平台确认该智能体已注册电脑工具，再对设备说一个简单测试任务，例如“列出工作目录中的文件，不要修改文件”。

安装器默认连接远程 `wss://` 地址；自建服务器必须提供兼容本程序的 MCP WebSocket 接入点。`connected` 只表明 WebSocket 已建立，请再验证工具注册、模型调用及结果播报。自建服务和设备绑定需要另行部署配置，安装脚本不会替客户创建或绑定云端智能体。

## 日常使用

未指定执行器时，桥接程序根据预设关键词选择已发现的 Agent；明确指定时优先遵循用户选择。每次只接收一个任务。用户可以说“当前任务是什么”查询概要，或说“查询任务1”。任务完成通知通过 MCP 尽力发送，平台是否主动播报取决于服务器实现。

在 PowerShell 查看状态：

```powershell
$root = Join-Path $env:LOCALAPPDATA 'XiaozhiComputerBridge'
powershell -ExecutionPolicy Bypass -File "$root\start_bridge.ps1" -Status
```

更新智能体或 MCP 接入点：

```powershell
$root = Join-Path $env:LOCALAPPDATA 'XiaozhiComputerBridge'
powershell -ExecutionPolicy Bypass -File "$root\start_bridge.ps1" -SetEndpoint
```

输入新地址后，先等待当前任务结束，再重启：

```powershell
powershell -ExecutionPolicy Bypass -File "$root\start_bridge.ps1" -Restart -Detached
```

设备仍需在平台绑定到新 MCP 地址所属的智能体。停止桥接程序使用：

```powershell
powershell -ExecutionPolicy Bypass -File "$root\start_bridge.ps1" -Stop
```

## 常见问题

- **HTTP 401/403**：平台拒绝了接入令牌；在自己的智能体中重新生成地址，使用 `-SetEndpoint` 更新。
- **未检测到 Agent**：安装并配置受支持的 Agent，然后重新运行安装程序。Kimi 等其他 Agent 暂未接入。
- **显示已连接，但小智没有调用工具**：确认设备绑定和 MCP 接入点属于同一智能体，并检查服务端 MCP 工具注册和语言模型工具调用能力。
- **任务一直无法确认**：超时或执行中断后，任务可能进入 `unknown`，阻止新的任务。先检查实际执行结果；当前版本没有面向客户的一键解除入口。
- **DeepSeek Harness**：需要先安装官方 `dsh` 并配置模型；本项目的真实端到端执行仍需在对应客户环境验证。

## 维护与构建

修改运行文件或本说明后，在项目目录运行 `powershell -ExecutionPolicy Bypass -File .\build-installer.ps1` 重新生成 `install.ps1`。此仓库仅包含电脑桥，设备固件和自建平台不包含在内。
