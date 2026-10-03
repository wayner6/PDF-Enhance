# PDF-Enhance · PDF 提升

PDF-Enhance 是一个可扩展的本地 PDF 处理工具，面向扫描文档、电子书、技术规范和长篇资料。

目前支持全书 OCR、双层可搜索 PDF、智能目录解析、页码偏移推算和精确书签定位；后续可以继续扩展页面修复、压缩、拆分合并、格式转换和 AI 文档理解等能力。

## 核心功能

- **本地 OCR**
  - 使用 RapidOCR 与 ONNX Runtime 在本机识别扫描页面；
  - 无须把整本 PDF 上传到外部服务；
  - 支持按算力档位选择并发核心数。

- **双层可搜索 PDF**
  - 保留原始扫描图片和版式；
  - 注入不可见文字层，支持搜索、复制和划词；
  - 使用 OCR 字符级坐标对齐搜索高亮位置。

- **智能目录与书签**
  - 分别识别目录标题区和右侧页码列，再按几何位置配对；
  - 自动推算逻辑页码与 PDF 物理页码之间的偏移；
  - 书签可跳转到章节在页面中的具体位置。

- **人工校对流程**
  - 可导出 `toc.txt`；
  - 支持修改标题、页码和层级后重新写入 PDF。

- **可选视觉 AI**
  - 支持 OpenAI 兼容接口；
  - 可配置支持视觉输入的模型辅助理解复杂目录。

## 环境要求

- Python 3.10 或更高版本；
- Windows、Linux 或 macOS；
- 全书 OCR 默认使用 CPU，本项目目前没有启用 NVIDIA GPU 推理。

## 安装依赖

```bash
pip install -r requirements.txt
```

## 运行

启动交互式向导：

```bash
python pdf_enhance.py
```

也可以直接传入 PDF：

```bash
python pdf_enhance.py "你的文档.pdf"
```

## Windows 桌面版

**用户使用：**可以单独运行 `PDF-Enhance.exe`（首次启动可能较慢），也可以解压 `PDF-Enhance-Windows.zip`，双击其中 `PDF-Enhance/PDF-Enhance.exe`；便携压缩包中的 exe 不能脱离旁边的 `_internal` 文件夹运行。不需要安装 Python。选择 PDF，选择处理模式并点击“识别目录”；目录页码可留空自动查找。在窗口内修改目录标题、页码和 Tab 缩进层级，确认页码偏移后点击“生成书签 PDF…”。默认自动模式对扫描件按需 OCR，只制作书签；要生成可搜索 PDF，选择“补全缺少的文字层”或“重做全文 OCR”。覆盖已有目标文件前会弹窗确认。

**Windows 构建：**在 Windows 机器上安装 Python 3.12（含 `py` 启动器），联网运行 `build_windows.bat`。脚本创建 `.venv`、安装依赖、运行测试，并用 PyInstaller 分别生成独立运行的 `dist/PDF-Enhance.exe` 和便携版 `dist/PDF-Enhance-Windows.zip`。重新运行会覆盖同名构建产物。GitHub Actions 工作流也会构建并上传这两个文件作为构建产物，**不会自动发布 Release**。请在干净的 Windows 机器上实际打开两个版本并测试 OCR、书签及中文 PDF 后再发布；构建脚本本身不能代替发行版验收。未签名的 exe 可能触发 Windows SmartScreen 提示。

**源码开发：**安装依赖后运行 `python pdf_enhance_gui.py`。桌面版目前只使用本地目录解析；视觉 AI 功能仅在命令行向导中提供。Windows 字体缺失或无法正确识别中文的场景，需要使用实际中文扫描件进行验收。

## 扫描 PDF 工作流

对于纯扫描或部分页面缺少文字层的 PDF，PDF-Enhance 提供：

1. **强制重新 OCR 全部页面**
   - 对每一页重新识别，适合已有文字层乱码、搜索错误或质量较差的扫描 PDF；
   - 对有全页扫描底图的页面，会先移除旧文字层，同时保持底图像素不变；
   - 选择该模式时，94 页文件会明确识别 94 页。

2. **仅补全缺少文字层的页面**
   - 保留已有文字层，只 OCR 尚无有效文字层的页面；
   - 例如 94 页中有 91 页文字层时，只识别剩余 3 页。

3. **轻量按需 OCR**
   - 不生成全文文字层；
   - 自动 OCR 探测目录页，并分别识别标题区和页码列；
   - 仅 OCR 章节预期页附近来推算偏移并定位书签坐标。

程序会检查文字数量、隐藏文字、全页扫描图和 Unicode 映射质量。即使每页都有文字，如果文字层包含大量控制字符、私用区字符或替换字符，也会提示文字层可能损坏并提供重新 OCR 选项。OCR 子进程失败时会列出失败页并停止生成。

## 目录校对文件

导出的目录文件采用缩进文本格式：

```text
1 总则    1
2 基本规定    3
3 消防给水与消火栓系统    6
```

使用 Tab 或空格缩进表示下级标题，行尾数字表示目录中的逻辑页码。

## AI 配置（可选）

编辑 `config.json`：

```json
{
  "general": {
    "dpi": 200,
    "max_ocr_workers": 4
  },
  "ai_toc": {
    "enabled": true,
    "base_url": "https://example.com/v1",
    "api_key": "your_api_key",
    "model": "your-vision-model"
  }
}
```

配置查找顺序为：环境变量 `PDF_ENHANCE_CONFIG` 指定路径、当前目录、项目目录、用户目录 `~/.config/pdf-enhance/config.json`。

配置的模型必须支持图片输入。未启用或调用失败时，PDF-Enhance 会使用本地 OCR 解析目录。

## 测试

安装开发依赖并运行完整测试：

```bash
pip install -e ".[dev]"
python -m pytest -q
```

## 输出安全

PDF-Enhance 禁止将输出路径设置成输入文件本身。输出会先写入同目录临时文件，完成后再替换目标文件，避免处理中断留下损坏的 PDF。
