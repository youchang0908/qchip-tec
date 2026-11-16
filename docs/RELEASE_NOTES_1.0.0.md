# Qchip TEC v1.0.0

Qchip TEC 的首个统一正式版本。

- 软件本体与云桥接合并到同一个 `Qchip TEC.exe`，共用版本号 1.0.0。
- 新增“版本与更新”：检查 GitHub Releases、下载校验、更新并重启；后续更新在原目录替换程序文件，保留本机配置、配对、日志和历史。
- 日志保存路径与文件名前缀统一由设置窗口底部的“保存设置”提交，取消则丢弃尚未保存的路径修改。
- 保留 Arroyo 7154 四通道和 5240 单通道支持、实时曲线、本地日志，以及可选的 Cloud Lab 桥接。

## 下载与使用

下载 `Qchip-TEC-Windows-x64.zip`，解压到长期使用的目录后运行 `Qchip TEC.exe`。GitHub 自动生成的 Source code 压缩包用于源码开发，不是 Windows 程序包。

更新重启会有短暂采集空档，设备连接需在重启后手动恢复。日志和本机配置会保留。

Author: Chang You  
Co-author: Codex  
License: MIT；第三方组件保留各自许可。

Qchip TEC is a Windows application for Arroyo TEC controllers, with real-time monitoring, local logging, optional Cloud Lab connectivity, and in-place GitHub Release updates.
