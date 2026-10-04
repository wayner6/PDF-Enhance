"""通用重复标题定位、OCR 复用和发行版输出规则。"""

import queue
from pathlib import Path

import pymupdf as fitz
import pytest

import pdf_enhance_gui as gui
from pdf_enhance_core.bookmark_writer import (
    apply_bookmarks, find_title_coordinate_on_page, find_title_coordinate_from_ocr_items,
)
from pdf_enhance_core.detector import detect_toc_pages_with_ocr
from pdf_enhance_core.offset_finder import detect_page_offset_with_ocr
from pdf_enhance_core.ocr_engine import cached_page_ocr
from pdf_enhance_core.parser import TOCItem, parse_toc_from_ocr_pages


def item(text, x, y):
    return {"text": text, "box": [[x, y], [x + 100, y], [x + 100, y + 15], [x, y + 15]]}


def test_pdf_heading_keeps_number_and_ignores_body_mentions():
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((80, 200), "See Shared Heading for details")
        page.insert_text((60, 500), "4. 7 Shared Heading")
        point = find_title_coordinate_on_page(page, "4.7 Shared Heading")
        assert point.x == 60
        assert 460 < point.y < 500


@pytest.mark.parametrize("lookalike", ["14.7 Shared Heading", "4.7.1 Shared Heading", "47 Shared Heading"])
def test_similar_numbers_do_not_match(lookalike):
    assert find_title_coordinate_from_ocr_items([item(lookalike, 60, 200)], "4.7 Shared Heading") is None
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 200), lookalike)
        page.insert_text((60, 400), "Shared Heading appears in body text")
        assert find_title_coordinate_on_page(page, "4.7 Shared Heading") is None


def test_page_cache_performs_ocr_once(monkeypatch):
    calls = []
    monkeypatch.setattr("pdf_enhance_core.ocr_engine.ocr_pdf_page", lambda page, dpi: (
        calls.append(page.number) or [item("1 Sample", 40, 100)], (595, 842)))
    with fitz.open() as doc:
        page = doc.new_page()
        cache = {}
        first, _ = cached_page_ocr(page, 200, cache)
        second, _ = cached_page_ocr(page, 180, cache)
        assert first is second
    assert calls == [0]


def test_all_bookmark_stages_reuse_full_ocr(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    cache = {
        0: [item("目录", 200, 60), item("1 绪论", 60, 100), item("(1)", 500, 100),
            item("2 方法", 60, 150), item("(2)", 500, 150)],
        1: [item("1 绪论", 60, 100)],
        2: [item("2 方法", 60, 300)],
    }
    def unexpected_ocr(*args, **kwargs):
        pytest.fail("重复调用整页 OCR")
    monkeypatch.setattr("pdf_enhance_core.ocr_engine.ocr_pdf_page", unexpected_ocr)
    monkeypatch.setattr("pdf_enhance_core.parser._ocr_right_margin_page_numbers", lambda *args, **kwargs: [(107.5, 1), (157.5, 2)])
    monkeypatch.setattr("pdf_enhance_core.parser._ocr_left_numbering_tokens", lambda *args: [])
    with fitz.open() as doc:
        for _ in range(3):
            doc.new_page(width=600, height=800)
        doc.save(source)
        assert detect_toc_pages_with_ocr(doc, ocr_cache=cache) == [1]
        items = parse_toc_from_ocr_pages(doc, [1], ocr_cache=cache)
        assert len(items) == 2
        offset, _ = detect_page_offset_with_ocr(doc, items, 1, ocr_cache=cache)
        assert offset == 1
    ok, count, message = apply_bookmarks(str(source), str(output), items, page_offset=1, ocr_cache=cache)
    assert ok, message
    assert count == 2
    with fitz.open(output) as doc:
        assert doc.get_toc(False)[1][3]["to"].y == 285


def test_combined_success_only_exports_final_pdf(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    working = workspace / "searchable.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((50, 50), "1 Sample heading with sufficient text")
        doc.save(source)
    def fake_ocr(source, output, **kwargs):
        Path(output).write_bytes(Path(source).read_bytes())
        kwargs["ocr_page_callback"](0, [item("1 Sample", 50, 50)])
        return True, "OCR complete"
    monkeypatch.setattr(gui, "generate_searchable_pdf", fake_ocr)
    monkeypatch.setattr(gui, "detect_toc_pages", lambda doc: [1])
    monkeypatch.setattr(gui, "parse_toc_from_pages", lambda *args: [TOCItem("1 Sample", 1)])
    monkeypatch.setattr(gui, "detect_page_offset", lambda *args: (0, ""))
    app = gui.PDFEnhanceApp.__new__(gui.PDFEnhanceApp)
    app.events = queue.Queue()
    result = app.process_worker(source, gui.MODES[1], working, 1)
    assert result[0] == "analysis"
    assert list(output_dir.iterdir()) == []
    output = gui.output_path(source, output_dir, gui.MODES[1])
    saved = app.save_worker(result[1], output, result[2], result[4], result[6], result[7])
    assert saved[0] == "saved"
    assert [path.name for path in output_dir.iterdir()] == ["source_ocr_bookmark.pdf"]


def test_fallback_copy_failure_preserves_existing_output(tmp_path, monkeypatch):
    target = tmp_path / "source_ocr.pdf"
    target.write_bytes(b"existing")
    def fail_copy(*args):
        raise OSError("copy interrupted")
    monkeypatch.setattr(gui.shutil, "copyfile", fail_copy)
    with pytest.raises(OSError):
        gui.export_ocr_fallback(tmp_path / "working.pdf", target)
    assert target.read_bytes() == b"existing"
    assert [p.name for p in tmp_path.iterdir()] == [target.name]
