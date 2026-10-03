import io
import os
import tempfile
from pathlib import Path

import pymupdf as fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

from pdf_enhance_core import (
    apply_bookmarks,
    detect_page_offset_with_ocr,
    detect_toc_pages_with_ocr,
    parse_toc_from_ocr_pages,
)

FONT_PATH = "C:/Windows/Fonts/simhei.ttf"


def _image_page(lines, font):
    image = Image.new("RGB", (600, 800), "white")
    draw = ImageDraw.Draw(image)
    for position, text, size in lines:
        line_font = ImageFont.truetype(FONT_PATH, size)
        draw.text(position, text, fill="black", font=line_font)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _create_lightweight_sample(path: Path):
    if not os.path.exists(FONT_PATH):
        pytest.skip("当前系统没有测试所需的中文字体")
    font = ImageFont.truetype(FONT_PATH, 24)
    document = fitz.open()
    pages = [
        [((180, 200), "示例规范", 36)],
        [
            ((260, 60), "目录", 32),
            ((60, 150), "1 总则", 24), ((510, 150), "1", 24),
            ((60, 200), "2 基本规定", 24), ((510, 200), "2", 24),
            ((60, 250), "3 消防系统", 24), ((510, 250), "3", 24),
        ],
        [((60, 80), "1 总则", 30), ((60, 150), "这里是正文内容", 22)],
        [((60, 80), "2 基本规定", 30), ((60, 150), "这里是正文内容", 22)],
        [((60, 80), "3 消防系统", 30), ((60, 150), "这里是正文内容", 22)],
    ]
    for lines in pages:
        page = document.new_page(width=600, height=800)
        page.insert_image(page.rect, stream=_image_page(lines, font))
    document.save(path)
    document.close()


def test_scanned_lightweight_mode_end_to_end():
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "scan.pdf"
        output = Path(directory) / "bookmarked.pdf"
        _create_lightweight_sample(source)
        with fitz.open(source) as document:
            toc_pages = detect_toc_pages_with_ocr(document, max_search_pages=5, dpi=180)
            assert toc_pages == [2]
            items = parse_toc_from_ocr_pages(document, toc_pages, dpi=180)
            assert [(item.title, item.logical_page) for item in items] == [
                ("1 总则", 1), ("2 基本规定", 2), ("3 消防系统", 3)
            ]
            offset, _ = detect_page_offset_with_ocr(
                document, items, toc_pages[-1], dpi=180, radius=2
            )
            assert offset == 2

        ok, count, message = apply_bookmarks(
            str(source), str(output), items, page_offset=offset,
            use_ocr_coordinates=True, ocr_dpi=180,
        )
        assert ok, message
        assert count == 3
        with fitz.open(output) as result:
            assert [row[:3] for row in result.get_toc()] == [
                [1, "1 总则", 3], [1, "2 基本规定", 4], [1, "3 消防系统", 5]
            ]
