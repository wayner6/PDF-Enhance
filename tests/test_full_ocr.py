import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

from pdf_enhance_core import check_pdf_text_layer, generate_searchable_pdf


def _create_scanned_pdf(path: Path) -> None:
    document = fitz.open()
    font_path = "C:/Windows/Fonts/simhei.ttf"
    font = ImageFont.truetype(font_path, 24) if os.path.exists(font_path) else None
    for page_index in range(1, 4):
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        if font:
            draw.text((50, 100), f"第{page_index}章 深度学习与视觉识别", fill="black", font=font)
            draw.text((50, 200), "这是纯图片扫描的一段测试正文文字...", fill="black", font=font)
        else:
            draw.text((50, 100), f"Chapter {page_index} Deep Learning", fill="black")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        page = document.new_page(width=600, height=800)
        page.insert_image(page.rect, stream=stream.getvalue())
    document.save(path)
    document.close()


def test_full_searchable_pdf():
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "scanned.pdf"
        output = Path(directory) / "searchable.pdf"
        _create_scanned_pdf(source)

        with fitz.open(source) as document:
            assert check_pdf_text_layer(document) is False

        ok, message = generate_searchable_pdf(str(source), str(output), max_workers=2)
        assert ok, message

        with fitz.open(output) as result:
            extracted = result[0].get_text()
            metadata = " ".join(str(value or "") for value in result.metadata.values())
            assert check_pdf_text_layer(result) is True
            assert "PDF-Enhance searchable OCR" in metadata
            assert "深度学习" in extracted.replace(" ", "") or "Chapter" in extracted.replace(" ", "")


if __name__ == "__main__":
    test_full_searchable_pdf()
    print("全书双层 OCR 测试通过")
