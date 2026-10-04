import os
import tempfile
from pathlib import Path

import pymupdf as fitz
import pytest

from pdf_enhance_core.config import load_config
from pdf_enhance_core.parser import (
    TOCItem,
    _parse_margin_page_token,
    clean_toc_title,
    extract_items_from_row_text,
)
from pdf_enhance_core.pdf_ocr_pipeline import (
    generate_searchable_pdf,
    remove_existing_text_from_scan_page,
)
from pdf_enhance_core.toc_io import import_from_toc_file
from pdf_enhance_core.bookmark_writer import apply_bookmarks
from pdf_enhance_core.detector import text_quality_stats


def test_config_environment_path(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.json"
        path.write_text('{"general":{"dpi":321,"max_ocr_workers":3}}', encoding="utf-8")
        monkeypatch.setenv("PDF_ENHANCE_CONFIG", str(path))
        config = load_config()
        assert config["general"] == {"dpi": 321, "max_ocr_workers": 3}


def test_invalid_toc_line_is_reported():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "toc.txt"
        path.write_text("1 总则\t1\n缺少页码\n", encoding="utf-8")
        with pytest.raises(ValueError, match="缺少有效页码"):
            import_from_toc_file(str(path))


def test_output_cannot_overwrite_input():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "source.pdf"
        document = fitz.open()
        document.new_page()
        document.save(path)
        document.close()
        ok, message = generate_searchable_pdf(str(path), str(path))
        assert not ok
        assert "不能与输入文件相同" in message


def test_title_cleanup_has_no_document_specific_replacement():
    assert clean_toc_title("于粉材料") == "于粉材料"
    assert clean_toc_title("天火研究") == "天火研究"


def test_formula_number_inside_title_is_not_treated_as_chapter_number():
    assert extract_items_from_row_text(
        "9 SF 6 气体循环再利用........................ 6"
    ) == [("9 SF 6 气体循环再利用", 6)]
    assert extract_items_from_row_text(
        "10 SF 6 混合气体循环再利用................... 8"
    ) == [("10 SF 6 混合气体循环再利用", 8)]


def test_broken_unicode_mapping_is_detected():
    class FakePage:
        def __init__(self, text):
            self.text = text

        def get_text(self):
            return self.text

    damaged = [FakePage(("abc\x81def" * 10)) for _ in range(4)]
    valid = [FakePage("这是可以正常搜索的中文文字。" * 5) for _ in range(4)]
    assert text_quality_stats(damaged) == (4, 4)
    assert text_quality_stats(valid) == (0, 4)


def test_logical_page_digits_are_preserved_for_excerpt_documents():
    assert _parse_margin_page_token("(166)") == 166
    assert _parse_margin_page_token("(144)") == 144
    assert _parse_margin_page_token("177") == 177
    assert _parse_margin_page_token("95") == 95


def test_preface_split_into_two_text_lines_is_found_after_toc_page():
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "source.pdf"
        output = Path(directory) / "output.pdf"
        document = fitz.open()
        document.new_page()
        toc_page = document.new_page()
        toc_page.insert_text((50, 50), "TOC")
        preface_page = document.new_page()
        preface_page.insert_text((50, 50), "前")
        preface_page.insert_text((50, 70), "言")
        document.save(source)
        document.close()
        item = TOCItem("前言", 99, source_pdf_page=2)
        ok, _, message = apply_bookmarks(str(source), str(output), [item], page_offset=0)
        assert ok, message
        with fitz.open(output) as result:
            assert result.get_toc()[0][2] == 3


def test_bookmark_writer_rejects_out_of_range_pages():
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "source.pdf"
        output = Path(directory) / "output.pdf"
        document = fitz.open()
        document.new_page()
        document.save(source)
        document.close()
        ok, _, message = apply_bookmarks(
            str(source), str(output), [TOCItem("1 Test", 99)], page_offset=0
        )
        assert not ok
        assert "超出 PDF 范围" in message


def test_old_scan_text_can_be_removed_without_changing_image():
    document = fitz.open()
    page = document.new_page(width=200, height=200)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200), False)
    pix.clear_with(240)
    page.insert_image(page.rect, pixmap=pix)
    page.insert_text((20, 40), "wrong hidden OCR", render_mode=3)
    pixels_before = page.get_pixmap(alpha=False).samples

    assert remove_existing_text_from_scan_page(page) is True
    assert page.get_text().strip() == ""
    assert page.get_pixmap(alpha=False).samples == pixels_before
    document.close()
