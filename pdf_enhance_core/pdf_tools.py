"""Local PDF compression and authenticated decryption using existing MuPDF APIs."""

import os
from pathlib import Path
import shutil
import tempfile

import pymupdf as fitz

from .cancellation import check_cancelled

COMPRESSION_PROFILES = ("无损优化", "均衡压缩（有损）", "强力压缩（有损）")
_IMAGE_SETTINGS = {
    COMPRESSION_PROFILES[1]: (250, 200, 80),
    COMPRESSION_PROFILES[2]: (200, 150, 65),
}


class PDFToolCleanupError(OSError):
    """Do not hide a leftover (possibly decrypted) temporary file as a normal cancellation."""


def compress_pdf(source, output, profile=COMPRESSION_PROFILES[0], password="", status_callback=None):
    if profile not in COMPRESSION_PROFILES:
        raise ValueError("请选择有效的压缩档位。")
    return _rewrite_pdf(source, output, password, profile, status_callback)


def remove_pdf_password(source, output, password="", status_callback=None):
    """Use a known user/owner password (or an empty user password), never guess it."""
    return _rewrite_pdf(source, output, password, None, status_callback)


def _rewrite_pdf(source, output, password, profile, status_callback):
    check_cancelled()
    source, output = Path(source), Path(output)
    if not source.is_file():
        raise ValueError("输入 PDF 不存在。")
    if not output.parent.is_dir():
        raise ValueError("输出目录不存在。")
    if source.resolve() == output.resolve() or (output.exists() and os.path.samefile(source, output)):
        raise ValueError("不能覆盖输入 PDF，请另存为新文件。")
    if output.exists() and not output.is_file():
        raise ValueError("输出目标不是文件。")
    original_size = source.stat().st_size
    temporaries = []
    try:
        with fitz.open(source) as doc:
            if not doc.is_pdf:
                raise ValueError("输入文件不是 PDF。")
            encrypted = bool(doc.needs_pass or (doc.metadata or {}).get("encryption"))
            if (doc.needs_pass or password) and not doc.authenticate(password):
                raise ValueError("PDF 密码错误或未提供密码；不会尝试破解。")
            if not len(doc):
                raise ValueError("PDF 没有可处理的页面。")
            if doc.get_sigflags() > 0:
                raise ValueError("PDF 含签名字段；重写可能破坏数字签名，暂不处理。")
            for page in doc:
                check_cancelled()
                if any(page.widgets(types=[fitz.PDF_WIDGET_TYPE_SIGNATURE]) or []):
                    raise ValueError("PDF 含签名字段；重写可能破坏数字签名，暂不处理。")
            if profile is None and not encrypted:
                raise ValueError("该 PDF 未加密，无需去除密码。")
            check_cancelled()
            if status_callback:
                status_callback("正在优化 PDF…" if profile else "正在生成无密码 PDF…")
            # Compare lossy rewriting against an actual lossless baseline, not just the input.
            for rewrite_images in ([False, True] if profile in _IMAGE_SETTINGS else [False]):
                check_cancelled()
                if rewrite_images:
                    threshold, target, quality = _IMAGE_SETTINGS[profile]
                    doc.rewrite_images(dpi_threshold=threshold, dpi_target=target, quality=quality,
                                       bitonal=False, set_to_gray=False)
                    check_cancelled()
                fd, temporary = tempfile.mkstemp(prefix=".pdf-enhance-tools-", suffix=".pdf", dir=output.parent)
                temporaries.append(temporary)
                os.close(fd)
                check_cancelled()
                doc.save(temporary, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True,
                         use_objstms=1, encryption=fitz.PDF_ENCRYPT_KEEP if profile else fitz.PDF_ENCRYPT_NONE)
        check_cancelled()
        temporary = min(temporaries, key=lambda path: Path(path).stat().st_size)
        # Equal sizes favor the first (lossless) result. Never trade quality for no benefit.
        if profile and Path(temporary).stat().st_size >= original_size:
            shutil.copyfile(source, temporary)
            message = "没有获得体积收益，已另存原文件副本，未降低图片质量。"
        elif profile:
            size = Path(temporary).stat().st_size
            message = f"压缩完成：{original_size / 1024 / 1024:.2f} → {size / 1024 / 1024:.2f} MB，减少 {1 - size / original_size:.1%}。"
            if len(temporaries) == 2 and temporary == temporaries[0]:
                message += " 有损结果未优于无损优化，已采用无损结果。"
            if encrypted:
                message += " 原有密码保护已保留。"
        else:
            message = "密码去除完成，已生成无密码 PDF；原文件未修改。"
        for unused in temporaries:
            if unused != temporary:
                os.remove(unused)
        check_cancelled()
        os.replace(temporary, output)
        return message
    finally:
        leftovers = []
        for temporary in temporaries:
            if os.path.exists(temporary):
                try:
                    os.remove(temporary)
                except OSError:
                    leftovers.append(temporary)
        if leftovers:
            raise PDFToolCleanupError(f"无法清理临时 PDF，请手动删除：{', '.join(leftovers)}")
