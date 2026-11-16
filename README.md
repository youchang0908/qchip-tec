# Qchip TEC

一个vibe coding的用于 Arroyo TEC 的 Windows 桌面温控软件。把实时读数、温度曲线、通道控制和日志放在同一个界面里，也可以通过云桥接远程查看和操作设备，美观性和易用性爆杀Arroyo原厂软件。赞美AI大人。

**Author:** Chang You（游畅）  
**Co-author:** Codex

contact me：youchang@pku.edu.cn

Qchip TEC is a Windows desktop application for Arroyo temperature controllers, with live measurements, plots, channel controls, local logging, optional cloud connectivity, and GitHub Release updates.

## 已知的支持设备

| 型号 | 通道数 |
| --- | --- |
| Arroyo 7154 | 4 |
| Arroyo 5240 | 1 |

连接时自动识别型号和通道数。其他型号尚未验证，你可以试试，能用告诉我。

## 主要功能

- 通道总览与详情，实时温度曲线和电学读数。
- 温度设定、输出开关、控制模式与 PID 设置，以及本机只读保护。
- 本地日志与历史查看，日志目录和记录间隔可设置。
- 可选云桥接、配对与控制权切换；临时断网后自动重连，人为断开保持关闭。
- 设置内检查 GitHub Releases，一次更新整套程序；安装位置、日志和本机配置保持不变。

## 开始使用

1. 从 [Releases](https://github.com/youchang0908/qchip-tec/releases/latest) 下载 Windows 程序包，解压到一个长期使用的目录。
2. 确认电脑具备设备所需的 VISA 驱动和 Microsoft WebView2 Runtime。已有的实验环境可以继续使用。
3. 打开 `Qchip TEC.exe`，在“设置 → 设备”中选择 COM 口并连接。
4. 查看自动识别的设备信息，按需要设置本机名称、日志目录和只读模式。

本地使用不需要网站账号。连接、断开和软件退出不会代替你切换 TEC 输出；退出应用不等于关闭仪器。首次使用请先确认设备型号、端口和当前通道状态。

### 从终端安装（Windows PowerShell）

首次安装时，先在希望存放软件的文件夹里打开 Windows PowerShell，再运行下面的一行命令。推荐短入口：

```powershell
irm https://cloud.youchang.space/install.ps1 | iex
```

也可直接从 GitHub 获取同一份安装脚本：

```powershell
irm https://raw.githubusercontent.com/youchang0908/qchip-tec/main/install.ps1 | iex
```

脚本会下载最新正式版本，校验程序包和文件哈希，在当前 PowerShell 工作目录创建 `Qchip-TEC` 文件夹，创建开始菜单入口并启动。无需管理员权限、Python、Git 或 GitHub CLI；设备通信驱动和 WebView2 Runtime 仍需具备。

例如，当前目录是 `D:\Apps`，就会安装到 `D:\Apps\Qchip-TEC`。新安装的配置与历史在该文件夹的 `data` 下，默认 CSV 日志在 `log` 下。

也可指定安装目录；`-InstallDir` 是完整安装路径，不会再套一层文件夹：

```powershell
& ([scriptblock]::Create((irm https://cloud.youchang.space/install.ps1))) -InstallDir 'D:\Apps\Qchip-TEC'
```

这两条命令用于首次安装；目标文件夹已存在时，脚本会退出并保留原文件，不能用它覆盖升级。后续更新使用软件内的“检查更新”。两个入口都读取 GitHub 根目录的同一份 `install.ps1`，程序包来自同一仓库的 GitHub Releases；短网址只作固定跳转，不另外托管脚本。

## 更新软件

1.0.0 的检查更新若显示 `FORBIDDEN`，请按[保留日志与配对的手动升级步骤](docs/UPGRADE_1.0.0_TO_1.0.1.md)先更新到1.0.1。首次启动需指定原配置目录，之后会记住；该问题已在1.0.1修复。

在设置中点击“检查更新”。正常采集和记录日志时也可检查并下载新版本。下载并校验完成后，点击“更新并重启”，确认“更新的时候会暂时中断采集，勿谓言之不预！”后才开始更新。取消则继续运行。更新只替换程序文件，不覆盖日志、配对信息或本机设置。

更新会正常结束当前采集与日志。重启后请手动重新连接设备；真实记录空档会保留，不补造样本，也不重放仪器参数。附着独立采集服务时，不提供自动安装。现有日志目录会继续使用，不随版本切换；更新重启期间会有短暂采集空档。正在进行不能中断的实验时，可以稍后再更新。

## 日志与本机数据

新安装的默认 CSV 日志目录为安装目录下的 `log`，配置、配对和本地历史放在 `data` 下。日志目录仍可自行指定。修改日志目录和文件名前缀后，点击设置右下角的“保存设置”；取消则丢弃尚未保存的路径修改。正在记录时，需先停止日志才能更换保存位置。

已有电脑通过旧版更新器原地升级时，继续使用该电脑原来的配置、配对和日志目录，不自动搬迁到新目录。

已有数据可使用[导入工具](docs/PORTABLE_IMPORT_1.0.1.md)先检查、再确认复制；源文件保留，不自动搬迁。导入不包含配对凭据。

设备身份、配对凭据和本机配置各机独立保存。复制程序包到另一台电脑不会带走原电脑的凭据，也不会自动获得网站上的设备权限。已有电脑正常更新且保留本机配置时，不需要重新配对。

## 云桥接（可选）

云桥接需要相应 Cloud Lab 服务的授权与配对。本地采样和日志可以在不连接云端时继续运行。具体细节可以联系游畅：youchang@pku.edu.cn。

## 开发与构建

主要使用 Python、PyVISA、React 和 pywebview，Windows 程序由 PyInstaller 打包。公共界面以固定版本随源码提供，构建时不从另一台电脑的开发目录读取文件。

具体环境、依赖和构建命令见 [docs/BUILD.md](docs/BUILD.md)；GitHub Release 资产格式与发布步骤见 [docs/RELEASING.md](docs/RELEASING.md)。测试使用模拟设备，修改代码和发布程序无需向真实仪器写入参数。

## License

MIT License. Copyright (c) 2026 Chang You.

第三方依赖保留各自的许可，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
