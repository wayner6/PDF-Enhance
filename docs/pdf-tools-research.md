# PDF 压缩与密码去除：调研和实现

## 结论

复用现有 PyMuPDF / MuPDF，不新增 Python 库或外部 exe，不复制其他应用的处理代码。两项功能均本地运行：压缩保留原有加密，去密码另存无加密副本；不实现密码猜测或破解。

## 已核查的开源代码

| 项目 | 已有实现与位置 | 许可 | 对本项目的取舍 |
| --- | --- | --- | --- |
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) | [1.28.2 document API](https://github.com/pymupdf/PyMuPDF/blob/1.28.2/docs/document.rst)、[对应实现](https://github.com/pymupdf/PyMuPDF/blob/1.28.2/src/__init__.py)：`save` / `ez_save` 无损优化，`rewrite_images` 图片重写，`authenticate` 解密验证 | AGPLv3 / 商业双许可 | 项目已经使用且已采用 AGPL，直接调用公开 API |
| [qpdf](https://github.com/qpdf/qpdf) | [加密实现说明](https://github.com/qpdf/qpdf/blob/main/manual/encryption.rst)：用户或所有者密码均可恢复加密密钥；支持解密、对象流和数据流优化 | Apache-2.0 | 成熟备选，当前不额外引入 Windows 可执行文件 |
| [pikepdf](https://github.com/pikepdf/pikepdf) | qpdf 的 Python 接口；[security.md](https://github.com/pikepdf/pikepdf/blob/main/docs/topics/security.md)、[images.md](https://github.com/pikepdf/pikepdf/blob/main/docs/topics/images.md) 说明密码、权限及图像处理 | MPL-2.0 | 能满足结构优化和去密码，但会增加库和本机组件，与现有 PyMuPDF 重叠 |
| [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) | [optimize.py](https://github.com/ocrmypdf/OCRmyPDF/blob/main/src/ocrmypdf/optimize.py) 区分数据流压缩与 JPEG/PNG/JBIG2 优化，使用 pikepdf 及外部工具 | MPL-2.0 | 借鉴职责分层，不搬入整套 OCR/外部工具链 |
| [pdfc](https://github.com/theeko74/pdfc) | [pdf_compressor.py](https://github.com/theeko74/pdfc/blob/master/pdf_compressor.py) 调用 Ghostscript `pdfwrite` 和 `PDFSETTINGS` | 仓库未检测到许可证 | 可参考调用策略；不复制未明确授权的代码，也不增加 Ghostscript 发行负担 |
| [janosh/pdf-compressor](https://github.com/janosh/pdf-compressor) | [main.py](https://github.com/janosh/pdf-compressor/blob/main/pdf_compressor/main.py) 调用 iLovePDF API | MIT | 会上传文件、需要服务凭据，不符合本地处理目标 |

本次核查包括上游代码与文档，不只是项目名称或搜索摘要。PyMuPDF `rewrite_images()` 在 1.26.1 源码中已存在，因此依赖下限调整为 `pymupdf>=1.26.1`。

## 实现落点

- `pdf_enhance_core/pdf_tools.py`：`compress_pdf()` 和 `remove_pdf_password()` 共用认证、签名检查、安全保存与清理逻辑。
- `pdf_enhance_gui.py`：增加“PDF 压缩”和“PDF 密码去除”，任务选择改为下拉框，按任务显示压缩档位或遮蔽的密码输入框。
- `tests/test_pdf_tools.py`：验证压缩效果、无损渲染、文字层、书签、链接、附件、表单、加密保留、RC4/AES 密码去除，以及中断和覆盖保护。

开发者可直接调用相同的核心接口（输出路径必须与输入不同）：

```python
from pdf_enhance_core import compress_pdf, remove_pdf_password

compress_pdf("input.pdf", "input_compressed.pdf")  # 默认无损
remove_pdf_password("encrypted.pdf", "input_unlocked.pdf", password=known_password)
```

### 压缩

1. 默认无损：`garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, use_objstms=1`，不重新识别、不将整页栅格化、不改图片像素。
2. 用户主动选择有损档位时调用 `rewrite_images()`：均衡使用阈值 250、目标 200 DPI、JPEG 质量 80；强力使用阈值 200、目标 150 DPI、JPEG 质量 65。
3. 不转灰度，不处理已紧凑的二值图像。图片目标 DPI 是传给引擎的参数，实际尺寸变化取决于图片及引擎采样方式，不能承诺每张图片都精确达到目标值。
4. 有损档位先保存无损基线，再生成图片重写候选；候选不比无损结果更小时采用无损结果，避免为相同体积降低质量。最佳结果仍不小于原文件时，输出原始字节副本并明确报告“没有体积收益”。
5. 压缩用 `PDF_ENCRYPT_KEEP` 保持原有密码和权限，不能悄悄变成密码去除。

### 密码去除

1. 输入已知用户密码或所有者密码；错误或缺失时停止，不猜测密码。
2. 仅有权限限制、用户密码为空的 PDF 可以直接认证读取。这不同于破解打开密码；只能处理有权修改的文档。
3. 使用 `PDF_ENCRYPT_NONE` 另存 `_unlocked.pdf`。未加密文件提示无需处理。
4. 密码不写入配置、日志或外部命令；提交任务后清空界面密码框。

### 保存与中断

- 输出使用 `_compressed.pdf` 或 `_unlocked.pdf`，与原文件不同；保护同路径、符号链接和硬链接到原文件的情形。
- 在输出目录中写临时 PDF，文档关闭后再次检查中断，再用 `os.replace()` 提交。
- 当前图片重写和 PDF 保存属于原生调用，中断可能需要等待该调用结束；最终提交前中断不会覆盖既有输出。
- 带签名字段的 PDF 暂不处理，避免重写破坏签名。无损指不降低可见内容质量，不保证字节相同、数字签名有效或 PDF/A 合规性。

## 本地验证

测试集目前 **87 项通过、2 项跳过**（Windows 窗口检查在 Linux 上跳过），覆盖 RC4-128、AES-128、AES-256，已知用户/所有者密码及空用户密码，原文件和既有输出保护，以及签名字段与临时文件清理失败。

一份既有的 130 页、45 个书签的 OCR 扫描测试件，原体积 26.77 MB：无损优化约 26.67 MB（减少 0.3%）；均衡图片重写不比无损更小，因此自动采用无损结果；强力结果约 26.52 MB（减少 0.9%）。所有页面的提取文字和书签保持一致，无损模式抽样渲染像素一致。该结果说明已压缩扫描件的进一步收益可能很小，不能保证显著或固定比例的压缩。

这两项功能已接入开发源码，尚未构建或发布新版 Windows 发行包。

## 未纳入

Ghostscript 重建整份 PDF、云压缩、JBIG2 有损字符聚类和暴力破解均不纳入本版。PDF 压缩不能保证固定比例；已优化的 JPEG/JBIG2 扫描件，无损压缩的收益可能很小。
