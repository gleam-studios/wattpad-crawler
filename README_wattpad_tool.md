# Wattpad Tool

统一 CLI：

- `search`：按关键词搜索 Wattpad 公开作品元数据，并按 `readCount -> voteCount -> commentCount` 排序
- `export`：对你明确提供 URL 且确认有授权的免费作品，导出英文版和中文版 `docx`

桌面 GUI（全中文界面）：

- `python3 /Users/griffith/Desktop/AI/WattpadToolProject/wattpad_app.py`
- mac 本机直接双击 `dist/Wattpad 中文工具箱.app`
- Windows 打包后双击 `WattpadTool.exe`
- 导出时会弹窗询问 ZIP 保存位置，默认打开系统下载文件夹
- 搜索默认不再保存 `json/csv`，只有你手动勾选时才会落盘

## 搜索

```bash
python3 /Users/griffith/Desktop/AI/WattpadToolProject/wattpad_tool.py search "hockey" \
  --max-results 20 \
  --json-out /Users/griffith/Desktop/AI/WattpadToolProject/wattpad_tool_output/hockey.json \
  --csv-out /Users/griffith/Desktop/AI/WattpadToolProject/wattpad_tool_output/hockey.csv
```

可选参数：

- `--include-mature`
- `--include-paywalled`
- `--page-size 50`

## 导出

```bash
python3 /Users/griffith/Desktop/AI/WattpadToolProject/wattpad_tool.py export "https://www.wattpad.com/story/242618522-ice-cold" \
  --authorized \
  --output-dir /Users/griffith/Desktop/AI/WattpadToolProject/wattpad_tool_output/export_test \
  --basename ice-cold-tool
```

默认输出：

- `*-en.html`
- `*-en.docx`
- `*-zh-cn.html`
- `*-zh-cn.docx`
- `*-metadata.json`

GUI 导出行为：

- 点击“开始导出”后，先选择 ZIP 压缩包保存位置
- 默认打开系统下载文件夹
- 程序会先生成稿件，再自动打包成一个 ZIP
- ZIP 内默认只保留最终英文 `docx` 和中文 `docx`

只导出英文版：

```bash
python3 /Users/griffith/Desktop/AI/WattpadToolProject/wattpad_tool.py export "https://www.wattpad.com/story/242618522-ice-cold" \
  --authorized \
  --skip-translation
```

## 边界

- 搜索只抓公开元数据
- 导出命令不支持直接从关键词搜索结果一键整本抓取
- 导出仅用于你拥有或获得明确授权的免费作品
- 对付费作品会直接拒绝导出

## 打包桌面应用

mac：

```bash
/Users/griffith/Desktop/AI/WattpadToolProject/build_macos.sh
```

产物默认在：

- `/Users/griffith/Desktop/AI/WattpadToolProject/dist/WattpadTool.app`
- `/Users/griffith/Desktop/AI/WattpadToolProject/dist/Wattpad 中文工具箱.app`
- `/Users/griffith/Desktop/AI/WattpadToolProject/dist/Wattpad 中文工具箱-mac.zip`

说明：

- `Wattpad 中文工具箱.app` 是本机可直接双击的包装应用，内部已经带上主程序
- `WattpadTool.app` 是底层 PyInstaller 产物，保留给构建和调试使用

mac 正式分发签名与 notarization：

```bash
/Users/griffith/Desktop/AI/WattpadToolProject/release_macos.sh
```

先做环境预检：

```bash
python3 /Users/griffith/Desktop/AI/WattpadToolProject/release_macos.py --check
```

这条链路会执行：

- `Developer ID Application` 签名
- `xcrun notarytool submit --wait`
- `xcrun stapler staple`
- 输出 `WattpadTool-notarized.zip`

前置条件：

- 钥匙串里有 `Developer ID Application` 证书
- 已用 `xcrun notarytool store-credentials WattpadToolNotary` 配好凭据

当前这台机器的实际状态：

- 有 `Apple Development` 证书
- 没有 `Developer ID Application` 证书
- 没有 `WattpadToolNotary` notary profile

所以现在可以正常开发和本机启动，但还不能完成 Apple 的正式分发 notarization。

Windows：

在 Windows 机器上运行以下任意一个：

```powershell
.\build_windows.ps1
```

或

```bat
build_windows.bat
```

Windows 产物会在：

- `dist/WattpadTool.exe`

## 当前已生成的 mac 应用

- `/Users/griffith/Desktop/AI/WattpadToolProject/dist/WattpadTool.app`
- `/Users/griffith/Desktop/AI/WattpadToolProject/dist/Wattpad 中文工具箱.app`
- `/Users/griffith/Desktop/AI/WattpadToolProject/dist/Wattpad 中文工具箱-mac.zip`
- `/Users/griffith/Desktop/AI/WattpadToolProject/启动WattpadTool.command`
