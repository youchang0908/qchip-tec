# 1.0.0 → 1.0.1：保留日志和配对的手动升级

用于同一台电脑上的现有1.0.0安装，尤其是“检查更新”显示 `FORBIDDEN` 时。可以覆盖程序文件，但第一次启动要明确沿用原配置目录；只覆盖后直接双击，可能会打开一份新的空配置。

## 1. 正常退出旧程序和桥接

如果正在记录日志，先正常结束记录。在系统托盘选择 Qchip TEC 的“退出程序”；窗口右上角的关闭按钮只会隐藏窗口。

在原来的程序文件夹中打开 PowerShell，用仍未替换的1.0.0程序通知旧桥接退出：

```powershell
Start-Process -FilePath (Join-Path (Get-Location).Path 'Qchip TEC.exe') -ArgumentList '--broker','exit' -Wait
```

等待旧后台进程退出后再覆盖；文件仍提示占用时先等待。以上操作会中断软件采集，不会切换仪器的TEC输出或修改温度/PID。

建议先将原程序文件夹、该电脑的 `%LOCALAPPDATA%\QchipTEC` 和实际CSV日志目录备份到本机另一位置。

## 2. 覆盖四个程序文件

从[1.0.1 Release](https://github.com/youchang0908/qchip-tec/releases/tag/v1.0.1)下载 `Qchip-TEC-Windows-x64.zip`，将里面这四个文件覆盖到原安装目录：

- `Qchip TEC.exe`
- `version.json`
- `LICENSE.txt`
- `THIRD_PARTY_NOTICES.txt`

保留原目录及其中的其他文件，保留原配置和日志位置。首次安装命令 `install.ps1` 不负责覆盖升级。

## 3. 第一次沿用旧配置启动

仍在这个原安装目录的 PowerShell 中执行：

```powershell
$tecBase = Join-Path $env:LOCALAPPDATA 'QchipTEC'
$tecLauncher = Join-Path $tecBase 'BridgeSettingsLauncher\launcher.json'
$tecProfile = if (Test-Path -LiteralPath $tecLauncher) { (Get-Content -LiteralPath $tecLauncher -Raw | ConvertFrom-Json).data_dir } else { Join-Path $tecBase 'Desktop' }
if (-not (Test-Path -LiteralPath (Join-Path $tecProfile '.runtime\settings.json'))) { throw '未找到原配置，停止启动。请先核对原来的数据目录。' }
& '.\Qchip TEC.exe' --data-dir $tecProfile
```

这段命令按1.0.0原有规则读取本机启动记录，未使用自选目录时选择原默认 `Desktop` 配置。1.0.1验证后会记住该选择，之后可以正常双击EXE或使用原快捷方式。若旧快捷方式曾手动添加其他 `--data-dir`，请先核对它与本机启动记录一致，不要猜一个目录启动。

## 4. 核对后重新连接

确认界面显示1.0.1、原设备名称与设置仍在，然后手动连接原COM口。原配对继续使用，无需生成新配对码；原CSV、SQLite历史和发布者身份继续使用，日志仍保存到原来的目录，不自动搬迁到新安装的 `log`。

本机配对/配置/历史所在目录须保留。若界面出现空配置或提示目录不匹配，先退出核对原路径；原文件仍在，不要重新配对或删除旧目录来尝试修复。
