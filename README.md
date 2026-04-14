# Wattpad 中文工具箱

[![Build and Release](https://github.com/gleam-studios/wattpad-crawler/actions/workflows/build-release.yml/badge.svg)](https://github.com/gleam-studios/wattpad-crawler/actions/workflows/build-release.yml)
[![Latest Release](https://img.shields.io/github/v/release/gleam-studios/wattpad-crawler)](https://github.com/gleam-studios/wattpad-crawler/releases/latest)
[![License](https://img.shields.io/badge/license-Private-lightgrey)](https://github.com/gleam-studios/wattpad-crawler)

面向中文用户的 Wattpad 桌面工具，提供两类能力：

- 按关键词搜索 Wattpad 公开作品元数据，并按热度排序；GUI 中可多选结果后一键批量导出
- 通过作品 URL 或批量流程导出英文 / 可选简体中文 Word 文档（使用者须自行确保有权存档与翻译）

项目同时提供：

- 全中文桌面 GUI
- CLI 命令行工具
- macOS / Windows 打包脚本
- GitHub Actions 自动构建流程

## 下载

- 最新版本：<https://github.com/gleam-studios/wattpad-crawler/releases/latest>
- 当前推荐下载：
  - macOS：`WattpadTool-mac.zip`
  - Windows：`WattpadTool-windows.zip`

## 界面预览

### 关键词搜索

![Wattpad 中文工具箱关键词搜索界面](docs/assets/search-preview.png)

### 授权导出

![Wattpad 中文工具箱授权导出界面](docs/assets/export-preview.png)

## 功能概览

### 1. 关键词搜索

- 搜索 Wattpad 公开作品元数据
- 按 `阅读量 -> 投票数 -> 评论数 -> 章节数` 排序
- 默认只在界面展示，不会额外生成 `json/csv`
- 如有需要，可手动勾选导出搜索结果

### 2. 搜索批量导出

- **搜索页**：结果列表支持多选，「导出选中作品」→ 确认数量、名称、是否生成中文与 Cookie（作者本人付费书）→ 选择 ZIP 保存位置；ZIP 内按作品分子文件夹存放各书的 Word 文档
- 默认生成英文版 `.docx`；**「中文」复选框默认不勾选**，需要简体中文版时请手动勾选（会调用翻译接口）
- 单 URL 导出仍可通过下方 CLI 完成

### 3. 跨平台打包

- macOS：生成 `Wattpad 中文工具箱.app`
- Windows：生成 `WattpadTool.exe`
- DOCX 导出已改为纯 Python 实现，不再依赖 macOS `textutil`

## 合规边界

- 搜索只抓取公开元数据
- 导出能力由使用者自行确保合法合规；工具不在界面中逐项核验版权
- 批量与单本导出均可能产生整本内容副本，请谨慎使用
- 对**他人**的付费作品不会提供导出能力
- **作者本人**的 Wattpad 付费作品：在浏览器登录作者账号并导出 Cookie 文件后，可使用 `--cookies`（CLI）或 GUI 中的「Cookie 文件」选项导出；工具会校验登录用户名与作品作者用户名一致

## 运行要求

- Python 3.12 推荐
- macOS 或 Windows
- 可访问 Wattpad 与 Google Translate 接口

安装运行依赖：

```bash
python3 -m pip install -r requirements.txt
```

如果要本地打包桌面应用：

```bash
python3 -m pip install -r requirements-build.txt
```

## 快速开始

### 启动桌面版

源码运行：

```bash
python3 wattpad_app.py
```

mac 本地打包后，直接双击：

- `dist/Wattpad.app`（启动器，内嵌 `WattpadTool.app`）

### CLI 搜索

```bash
python3 wattpad_tool.py search "hockey" \
  --max-results 20 \
  --json-out ./wattpad_tool_output/hockey.json \
  --csv-out ./wattpad_tool_output/hockey.csv
```

常用参数：

- `--include-mature`
- `--include-paywalled`
- `--page-size 50`

### CLI 导出

```bash
python3 wattpad_tool.py export "https://www.wattpad.com/story/242618522-ice-cold" \
  --output-dir ./wattpad_tool_output/export_test \
  --basename ice-cold
```

作者导出**本人**的付费作品时，需加上从已登录浏览器导出的 Cookie 文件（Netscape `cookies.txt` 或常见扩展导出的 JSON 列表）：

```bash
python3 wattpad_tool.py export "https://www.wattpad.com/story/你的作品链接" \
  --cookies ./wattpad_cookies.txt \
  --output-dir ./wattpad_tool_output/my_paid_story
```

同时生成简体中文版（需可访问翻译接口）：

```bash
python3 wattpad_tool.py export "https://www.wattpad.com/story/242618522-ice-cold" \
  --translate-zh
```

CLI **默认仅英文**；加上 `--translate-zh` 后才会额外生成中文 HTML/DOCX。旧版脚本里的 `--skip-translation` 已无实际作用（默认已是仅英文）。

默认会在输出目录生成：

- `*-en.html`
- `*-en.docx`
- `*-metadata.json`

启用 `--translate-zh` 时还会生成：

- `*-zh-cn.html`
- `*-zh-cn.docx`

搜索批量导出的 ZIP 内按作品分子目录存放各书文档；是否含中文版与 GUI 中「中文」勾选一致。

## 项目结构

```text
.
├── wattpad_app.py              # 全中文桌面 GUI
├── wattpad_tool.py             # 统一 CLI 入口
├── wattpad_cookies.py          # 从浏览器导出文件加载 Cookie（作者导出付费书）
├── wattpad_export.py           # 英文导出逻辑
├── translate_wattpad_html.py   # 中文翻译逻辑
├── docx_renderer.py            # 跨平台 DOCX 渲染
├── package_app.py              # PyInstaller 打包入口
├── build_macos.sh              # mac 打包脚本
├── build_windows.ps1           # Windows PowerShell 打包脚本
├── build_windows.bat           # Windows 批处理打包脚本
├── release_macos.py            # mac 正式签名 / notarization 辅助脚本
├── requirements.txt            # 运行依赖
├── requirements-build.txt      # 打包依赖
└── .github/workflows/          # GitHub Actions
```

## 本地打包

### macOS

```bash
./build_macos.sh
```

默认产物：

- `dist/WattpadTool.app`（PyInstaller 主程序包）
- `dist/Wattpad.app`（本地启动器，便于双击运行）
- `dist/WattpadTool-mac.zip`（分发用 zip，内含启动器）

### Windows

PowerShell：

```powershell
.\build_windows.ps1
```

或批处理：

```bat
build_windows.bat
```

默认产物：

- `dist/WattpadTool.exe`

## GitHub Actions

仓库内置自动构建流程：

- `push` 到 `main` 时自动构建 macOS / Windows 版本
- `pull_request` 时自动验证能否成功打包
- `workflow_dispatch` 可手动触发
- 推送 `v*` 标签时自动构建并发布 Release 资产

说明：

- macOS CI 产物是未 notarize 的构建包，可用于测试与分发前验证
- 如果需要 Apple 正式分发，还需要 `Developer ID Application` 证书和 notarization 凭据

## 当前仓库状态

- 已支持跨平台 Word 导出
- 已支持 mac 本机双击启动包装应用
- 已默认收敛搜索与导出产物，避免目录杂乱
- GUI 与 CLI 导出默认仅英文；中文翻译为可选（GUI 勾选「中文」或 CLI 使用 `--translate-zh`）

## 许可证与责任

本工具用于处理你拥有版权或已获得明确授权的内容。使用者需自行遵守目标平台条款、版权规则与当地法律。
