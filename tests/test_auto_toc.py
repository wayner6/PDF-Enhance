"""目录括号页码、拆分标题与 OCR 输出保留的回归测试。"""

import os
import queue
from pathlib import Path

import pymupdf as fitz
import pytest

import pdf_enhance_gui as gui
from pdf_enhance_core.detector import detect_toc_pages_with_ocr, score_toc_text
from pdf_enhance_core.parser import TOCItem, _parse_margin_page_token, extract_items_from_row_text


def test_split_heading_and_parenthesized_pages():
    first = "目\n次\n1\n总则\n(1)\n2\n基本规定\n(3)\n2.1\n目标与功能\n(3)"
    continuation = "6.5\n建筑装修\n(28)\n6.6\n建筑保温\n(30)\n7\n安全疏散\n(33)"
    assert score_toc_text(first)[0]
    assert score_toc_text(continuation)[0]
    assert not score_toc_text("1\n正文条款\n2\n正文条款\n3\n正文条款")[0]
    assert not score_toc_text("UDC\nGB55037-2022\n建筑防火通用规范\n2023-06-01")[0]


@pytest.mark.parametrize("token, expected", [("(12)", 12), ("（3）", 3), ("...(IV)", 4), ("12", 12)])
def test_margin_page_brackets(token, expected):
    assert _parse_margin_page_token(token) == expected


def test_text_toc_brackets():
    assert extract_items_from_row_text("1 总则 ........ (1)") == [("1 总则", 1)]
    assert extract_items_from_row_text("2 基本规定    （3）") == [("2 基本规定", 3)]


def test_ocr_detector_handles_split_contents(monkeypatch):
    texts = ["GB\n标准封面", "目\n次\n1\n总则\n(1)\n2\n基本规定\n(3)\n3\n建筑布局\n(9)",
             "6.5\n装修\n(28)\n6.6\n保温\n(30)\n7\n安全疏散\n(33)", "1\n正文\n2\n正文\n3\n正文"]
    monkeypatch.setattr("pdf_enhance_core.ocr_engine.ocr_pdf_page", lambda page, dpi: (
        [{"text": text} for text in texts[page.number].splitlines()], (600, 800)))
    with fitz.open() as doc:
        for _ in texts:
            doc.new_page()
        assert detect_toc_pages_with_ocr(doc) == [2, 3]


@pytest.mark.parametrize("stage", ["detection", "parsing", "offset"])
def test_combined_failure_preserves_ocr(tmp_path, monkeypatch, stage):
    source = tmp_path / "source.pdf"
    output = tmp_path / "source_ocr.pdf"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    working = workspace / "searchable.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((40, 40), "Existing PDF with sufficiently long readable text")
        doc.save(source)

    def fake_ocr(source, output, **kwargs):
        Path(output).write_bytes(Path(source).read_bytes())
        return True, "OCR done"

    def fail_offset(*args):
        raise ValueError("无法计算偏移")

    monkeypatch.setattr(gui, "generate_searchable_pdf", fake_ocr)
    monkeypatch.setattr(gui, "detect_toc_pages", lambda doc: [] if stage == "detection" else [1])
    monkeypatch.setattr(gui, "detect_toc_pages_with_ocr", lambda *args, **kwargs: [])
    monkeypatch.setattr(gui, "parse_toc_from_pages", lambda *args: [] if stage == "parsing" else [TOCItem("1 总则", 1)])
    monkeypatch.setattr(gui, "parse_toc_from_ocr_pages", lambda *args, **kwargs: [])
    monkeypatch.setattr(gui, "detect_page_offset", fail_offset)
    app = gui.PDFEnhanceApp.__new__(gui.PDFEnhanceApp)
    app.events = queue.Queue()
    result = app.process_worker(source, gui.MODES[1], working, 1)
    assert result[0] == "ocr_only"
    assert result[1].startswith("错误：")
    assert not output.exists()
    assert result[2] == working
    exported = app.fallback_worker(working, output, result[1])
    assert exported[0] == "ocr_saved"
    assert "全文 OCR 已完成并保存" in exported[1]
    with fitz.open(output) as doc:
        assert len(doc) == 1


def test_split_left_numbering_is_rebuilt(monkeypatch):
    from pdf_enhance_core.parser import _ocr_left_numbering_tokens
    def box(x):
        return [[x, 100], [x + 10, 100], [x + 10, 120], [x, 120]]
    monkeypatch.setattr("pdf_enhance_core.ocr_engine.get_ocr_engine", lambda: (
        lambda *args, **kwargs: ([[box(10), "1", .99], [box(22), "0", .99]], None)))
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        tokens = _ocr_left_numbering_tokens(page, dpi=72)
    assert tokens == [(110, "10")]


def test_missing_chapter_prefix_is_repaired(monkeypatch):
    from pdf_enhance_core.parser import parse_toc_from_ocr_pages
    monkeypatch.setattr("pdf_enhance_core.ocr_engine.ocr_pdf_page", lambda *args, **kwargs: (
        [{"text": "0 电气", "box": [[60, 100], [160, 100], [160, 120], [60, 120]]}], (600, 800)))
    monkeypatch.setattr("pdf_enhance_core.parser._ocr_left_numbering_tokens", lambda *args: [(110, "10")])
    monkeypatch.setattr("pdf_enhance_core.parser._ocr_right_margin_page_numbers", lambda *args, **kwargs: [(110, 53)])
    with fitz.open() as doc:
        doc.new_page(width=600, height=800)
        items = parse_toc_from_ocr_pages(doc, [1])
    assert [(item.title, item.logical_page) for item in items] == [("10 电气", 53)]


def test_error_prefix_is_chinese():
    app = gui.PDFEnhanceApp.__new__(gui.PDFEnhanceApp)
    app.events = queue.Queue()
    def fail():
        raise ValueError("未找到目录页")
    app.run_job(fail)
    assert app.events.get() == ("error", "错误：未找到目录页")


def test_paths_use_native_separators():
    assert gui.display_path("D:/UserData/Documents/book.pdf") == os.path.normpath("D:/UserData/Documents/book.pdf")
    if os.name == "nt":
        assert gui.display_path("D:/UserData/Documents") == gui.display_path(r"D:\UserData\Documents")
