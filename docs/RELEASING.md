# 发布与升级

本项目通过 GitHub Releases 发布 Windows 程序。

## 首次0.x → 1.0.0

0.x没有更新按钮。用户正常退出旧 native 和旧桥接后，把完整1.0.0程序包解压到一个长期使用的可写目录，更新快捷方式，再打开 `Qchip TEC.exe`。保留原本机 profile、配对和日志目录；不迁移、合并、覆盖任何历史文件。原启动器的自选 data_dir 会被读取并沿用。新启动默认不连接 COM，由用户手动连接，TEC 输出状态不变。

## 以后发布

修改 `src/version.py`，让正式 tag 为对应的 `vMAJOR.MINOR.PATCH`。先完成离线构建/测试和源码/资产审查，再经用户批准发布。仓库目标由 `src/release_config.py` 指定为 `youchang0908/qchip-tec`；不嵌入个人 PAT，也不使用网站凭据。

`.github/workflows/release.yml` 使用固定版本 Actions：在 `release.published` 后构建并向同一个 Release 附加下面三个资产。已有同名资产时上传失败，不自动 `--clobber`。失败后请维护者核对原因和已上传资产，勿静默覆盖。发布后检查 Actions 构建结果，并确认三个资产全部上传完成。

| 资产 | 内容 |
|---|---|
| Qchip-TEC-Windows-x64.zip | 完整程序，根目录是一个 Qchip TEC.exe、version.json、LICENSE.txt、THIRD_PARTY_NOTICES.txt |
| update.json | schema1、product QchipTEC、version、platform windows-x64、archive `{name,bytes,sha256}`、files `[{path,bytes,sha256}]` |
| SHA256SUMS.txt | ZIP 与 update.json 的 SHA256 |

最新正式 Release 没有资产时，应用显示“构建中，请稍后重试”。不存在仓库或正式 Release 时显示“尚未发布更新”；不会把 GitHub 自动源码 ZIP 当成程序包。

## 更新边界

检查/下载不停止采集。用户点击“更新并重启”后才正常结束采集、落盘日志并退出对应桥接。临时 helper 等待并核对旧 PID 的程序路径，不 kill 其他进程；等待超时不替换。只替换上表四个允许的程序文件，备份原文件，失败回退并重启旧版，同时明确提示失败。日志、数据、private profile、SQLite、配对和报警 sidecar 都不在替换范围内；不遍历安装目录做递归覆盖。

更新入口只在本机 token/Host/Origin 保护的 UI 中，客户端不能提供任意下载 URL 或安装路径；云桥接 typed commands 没有更新命令。附着独立/headless source 或旧 broker 时不自动替换。

下载仅使用指定 GitHub 仓库正式 Release，严格 HTTPS/重定向主机、大小、文件清单、SHA256 与版本核对。信任边界是发布者控制的 GitHub HTTPS 资产，不声称已有代码签名/清单签名。SHA 校验不等于独立的发布者签名。

重启后用户需手动连接设备。采集不是无缝连续的，真实记录空档保留，不补造样本，不重放温控参数。

## 一行安装入口

根目录 `install.ps1` 是首次安装脚本，随干净源码进入公开仓库 main 分支。README 同时保留两个入口：推荐短地址 `https://cloud.youchang.space/install.ps1` 和直接 GitHub 地址 `https://raw.githubusercontent.com/youchang0908/qchip-tec/main/install.ps1`。短地址只作固定重定向到后者，不另外托管脚本。两个入口都在正式发布后启用；当前未公开仓库、未发布或部署入口。不接收任意脚本 URL，不经过设备鉴权或遥测通道。

脚本读取同一仓库最新正式 Release，验证 update.json/ZIP/四个程序文件，在当前用户固定目录首次安装。已有目标目录不覆盖，应用内升级机制保持不变。系统驱动/WebView2 不由该脚本安装。

`tests/test_install.ps1` 只使用临时假目录、假下载与禁用启动，验证成功安装、下载哈希错误与已有目录保护。首次真实 GitHub 下载仍待公开发布阶段核验；不在实验室正式配置目录运行安装器。
