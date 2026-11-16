# Windows 构建

使用 Windows x64、Python3.9.13、Node22。实验室 Python/驱动不需要升级；在独立构建环境安装锁定依赖。仓库不包含任何实验 profile、密钥或原始数据。

```powershell
py -3.9 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.lock
npm ci
.\.venv\Scripts\python.exe scripts/collect_notices.py node_modules
.\.venv\Scripts\python.exe scripts/run_tests.py
powershell -NoProfile -File tests/test_install.ps1
.\.venv\Scripts\python.exe scripts/build_release.py --version 1.0.0 --tag v1.0.0 --output artifacts
```

`src/version.py` 是产品版本唯一来源；tag、参数和该值必须一致，否则构建失败。npm 的0.0.0仅标识前端构建工具配置，不是产品版本。公共 UI 固定1.4.1，构建会核验 `packaging/TEC_UI_PIN.json` 的完整文件哈希。

构建会在全新工作目录编译 native 与桥接界面，再生成一个 `Qchip TEC.exe`，检查 native 和 `--broker` 角色的版本，并验证桥接未加载仪器模块。统一 EXE 的归档本身包含 native 模块，这是正常的；运行角色通过入口与导入 guard 隔离。

输出 `artifacts/Qchip-TEC-Windows-x64.zip`、`update.json` 和 `SHA256SUMS.txt`。已有发布资产不会被默认覆盖。测试与编译不连接真实 COM。`scripts/smoke_exe.py` 可对实际 EXE 做1/4通道 fake 冒烟，`scripts/test_settings_ui.py` 使用独立临时 Edge 测试设置窗口；测试进程、profile 和端口均与实机隔离。

本地可用 `QCHIP_NODE` 指定已有 Node，`QCHIP_NODE_MODULES` 指定同一依赖锁的已有模块目录。这些仅是构建环境变量，不写入程序。CI 默认使用仓库 `node_modules`。

第三方许可见根目录 `THIRD_PARTY_NOTICES.md` 与 `docs/third_party/`。保留 pystray LGPL 正文；可通过 `pip download --no-binary :all: --no-deps pystray==0.19.5` 获取对应上游源码，并使用本仓库源码/锁定环境替换库后重新构建。不禁止为修改 LGPL 组件进行分析或重建。Microsoft WebView2 SDK1.0.2957.106 的 LICENSE/NOTICE 已保留；系统 WebView2 Runtime、VISA 驱动、系统字体和仪器手册不在发布包内。
