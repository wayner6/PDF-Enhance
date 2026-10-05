# 第三方许可与源码

PDF-Enhance 1.0.0 按 GNU Affero General Public License v3.0（AGPL-3.0-only）发布。Copyright (C) 2026 PDF-Enhance contributors。本软件不提供任何担保。项目许可见 `LICENSE`。

项目对应源码与构建脚本：
https://github.com/wayner6/PDF-Enhance/tree/v1.0.0

Windows 发行包中的 `notices` 目录（便携版位于 `_internal/notices`）以及单独发布的 `PDF-Enhance-Licenses.zip` 包含项目许可、第三方许可文件及 `BUILD_DEPENDENCIES.json`。该 JSON 记录构建环境的依赖版本、上游项目地址和该版本的 PyPI 文件页面；如上游提供 source distribution，可从对应页面下载，否则使用记录的上游源码地址。MuPDF 的确切版本及完整源码下载地址另列在 JSON 的 `mupdf` 字段中。构建工具和测试依赖也会记录，不表示全部包都被打入 exe。

## 主要组件

第三方代码、模型和本机库保留各自的版权与许可，项目的 AGPL 许可不替代其原有许可。

- **PyMuPDF / MuPDF**：AGPLv3 或 Artifex 商业授权；本发行版采用 AGPLv3。PyMuPDF 与 MuPDF 的源码需分别获取；PyMuPDF 源码包含构建说明，MuPDF 的完整源码下载地址见 `BUILD_DEPENDENCIES.json` 的 `mupdf.source_archive`。
  - https://github.com/pymupdf/PyMuPDF
  - https://github.com/ArtifexSoftware/mupdf
- **RapidOCR / OCR 模型**：Apache-2.0，Copyright (c) 2021 RapidOCR Authors。模型来自 PaddleOCR；许可见 `licenses/RapidOCR-LICENSE.txt`。
  - https://github.com/RapidAI/RapidOCR
  - https://github.com/PaddlePaddle/PaddleOCR
- **ONNX Runtime**：MIT；发行包中保留其 LICENSE 与 ThirdPartyNotices。
  - https://github.com/microsoft/onnxruntime
- **OpenCV**：Apache-2.0；发行包中保留其许可证及第三方许可，包括其本机库引用的其他组件。OpenCV Python 构建脚本及第三方库来源见：
  - https://github.com/opencv/opencv-python
  - https://github.com/opencv/opencv
  - https://github.com/opencv/opencv_3rdparty
- **NumPy**：BSD-3-Clause，另含本机库的许可说明；发行包中保留各许可文件。
  - https://github.com/numpy/numpy
- **Pillow**：MIT-CMU（历史 PIL 许可）；发行包中保留其许可证。
  - https://github.com/python-pillow/Pillow
- **FlatBuffers**：Apache-2.0；许可见 `licenses/FlatBuffers-LICENSE.txt`。
  - https://github.com/google/flatbuffers
- **Python / Tcl / Tk**：分别保留 Python 与 Tcl/Tk 的许可文件；无需用户安装 Python。
  - https://www.python.org/downloads/source/
  - https://www.tcl-lang.org/software/tcltk/download.html
- **PyInstaller**：GPLv2 或之后版本及其 bootloader exception；保留其许可与例外说明。
  - https://github.com/pyinstaller/pyinstaller

完整构建依赖与各自许可声明以发行包中的 `BUILD_DEPENDENCIES.json` 和 `licenses` 子目录为准。未经修改的依赖源码可按记录的版本从上游获取；PDF-Enhance 自身的完整源码由本仓库的 `v1.0.0` 标签提供。
