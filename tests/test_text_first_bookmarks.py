"""已 OCR 扫描件、混合页、乱码页及自动匹配失败的任务路由。"""

import queue

import pymupdf as fitz
import pytest

import pdf_enhance_gui as gui
from pdf_enhance_core.detector import usable_page_text, score_toc_text, detect_toc_pages_with_ocr
from pdf_enhance_core.offset_finder import detect_page_offset, detect_page_offset_with_ocr
from pdf_enhance_core.parser import TOCItem, toc_items_need_ocr


def ocr_item(text, x=60, y=100):
    return {"text": text, "box": [[x, y], [x + 100, y], [x + 100, y + 15], [x, y + 15]]}


def create_book(path, last_page="text", hidden=True):
    with fitz.open() as doc:
        for index in range(3):
            page = doc.new_page(width=600, height=800)
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 2, 2), False)
            pix.clear_with(255)
            page.insert_image(page.rect, pixmap=pix)
            mode = 3 if hidden else 0
            if index == 0:
                page.insert_text((240, 70), "目录", fontname="china-s", render_mode=mode)
                for y, text in ((120, "1 ALPHA ....... (1)"), (160, "1.1 DETAILS ....... (1)"), (200, "2 BETA ....... (2)")):
                    page.insert_text((60, y), text, render_mode=mode)
            elif index == 1:
                page.insert_text((60, 100), "1 ALPHA", render_mode=mode)
                page.insert_text((60, 300), "1.1 DETAILS", render_mode=mode)
            elif last_page == "text":
                page.insert_text((60, 100), "2 BETA", render_mode=mode)
            elif last_page == "damaged":
                font = fitz.Font("china-s")
                xref = page.insert_font(fontname="broken", fontbuffer=font.buffer)
                page.insert_text((60, 100), "BROKEN", fontname="broken", render_mode=mode)
                _, reference = doc.xref_get_key(xref, "ToUnicode")
                mappings = "\n".join(f"<{font.has_glyph(ord(char)):04x}> <e000>" for char in "BROKEN")
                cmap = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
                        "/CMapName /Broken def /CMapType 2 def\n"
                        "1 begincodespacerange <0000> <ffff> endcodespacerange\n"
                        f"6 beginbfchar\n{mappings}\nendbfchar\n"
                        "endcmap CMapName currentdict /CMap defineresource pop end end")
                doc.update_stream(int(reference.split()[0]), cmap.encode("ascii"))
            else:
                page.insert_text((280, 780), "2", render_mode=mode)
        doc.save(path)


def app_without_window():
    app = gui.PDFEnhanceApp.__new__(gui.PDFEnhanceApp)
    app.events = queue.Queue()
    return app


@pytest.mark.parametrize("hidden", [False, True])
def test_healthy_scan_text_never_invokes_ocr(tmp_path, monkeypatch, hidden):
    source = tmp_path / "book.pdf"
    output = tmp_path / "book_bookmark.pdf"
    create_book(source, hidden=hidden)
    before = source.read_bytes()
    def unexpected(*args, **kwargs):
        pytest.fail("正常文字层不应重新 OCR")
    monkeypatch.setattr("pdf_enhance_core.ocr_engine.ocr_pdf_page", unexpected)
    monkeypatch.setattr(gui, "generate_searchable_pdf", unexpected)
    app = app_without_window()
    result = app.process_worker(source, gui.MODES[2], output, 1)
    assert result[0] == "analysis"
    assert len(result[2]) == 3 and result[4] == 1
    assert not result[7]
    assert app.save_worker(result[1], output, result[2], result[4], result[6], result[7])[0] == "saved"
    assert source.read_bytes() == before
    assert not (tmp_path / "book_ocr.pdf").exists()
    with fitz.open(output) as doc:
        assert len(doc[0].get_image_info()) == 1
        assert doc.get_toc()[2][2] == 3


@pytest.mark.parametrize("last_page", ["missing", "damaged"])
def test_only_unusable_page_is_ocrd(tmp_path, monkeypatch, last_page):
    source = tmp_path / "book.pdf"
    output = tmp_path / "book_bookmark.pdf"
    create_book(source, last_page=last_page)
    with fitz.open(source) as doc:
        assert not usable_page_text(doc[2])
    calls = []
    def fake_ocr(page, dpi):
        calls.append(page.number)
        assert page.number == 2
        return [ocr_item("2 BETA")], (600, 800)
    monkeypatch.setattr("pdf_enhance_core.ocr_engine.ocr_pdf_page", fake_ocr)
    app = app_without_window()
    result = app.process_worker(source, gui.MODES[2], output, 1)
    assert result[0] == "analysis"
    assert calls == [2]
    app.save_worker(result[1], output, result[2], result[4], result[6], result[7])
    assert calls == [2]
    with fitz.open(output) as doc:
        assert doc.get_toc(False)[2][3]["to"].y == 85


def test_failed_native_title_position_retries_only_target(tmp_path, monkeypatch):
    source = tmp_path / "book.pdf"
    output = tmp_path / "book_bookmark.pdf"
    create_book(source)
    with fitz.open(source) as doc:
        page = doc[2]
        # 文本可读但不是目标标题；模拟映射或排版导致全文匹配失败。
        page.add_redact_annot(page.rect, fill=False)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        page.insert_text((60, 100), "Other readable text")
        doc.save(tmp_path / "changed.pdf")
    calls = []
    monkeypatch.setattr("pdf_enhance_core.ocr_engine.ocr_pdf_page", lambda page, dpi: (
        calls.append(page.number) or [ocr_item("2 BETA")], (600, 800)))
    app = app_without_window()
    result = app.process_worker(tmp_path / "changed.pdf", gui.MODES[2], output, 1)
    assert calls == []  # 其他两个正常标题已经可靠确定偏移。
    app.save_worker(result[1], output, result[2], result[4], result[6], result[7])
    assert calls == [2]


def test_fragmented_native_numbering_is_parsed_without_ocr(tmp_path):
    source = tmp_path / "toc.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        for x, text in ((60, "4"), (70, "."), (80, "2"), (110, "DETAILS"),
                        (420, "("), (430, "1"), (440, "2"), (450, ")")):
            page.insert_text((x, 100), text)
        doc.save(source)
        items = gui.parse_toc_from_pages(doc, [1])
    assert [(item.title, item.logical_page) for item in items] == [("4.2 DETAILS", 12)]


def test_numbering_contradiction_retries_but_zero_chapter_is_allowed():
    assert toc_items_need_ocr([TOCItem("1 AL�HA", 1)])
    assert toc_items_need_ocr([TOCItem("0 INTRO", 1), TOCItem("10.1 DETAILS", 2)])
    assert not toc_items_need_ocr([TOCItem("0 INTRO", 1), TOCItem("0.1 DETAILS", 2)])
    assert not toc_items_need_ocr([TOCItem("6.5 DETAILS", 2), TOCItem("6.6 MORE", 3)])


def test_body_clause_numbers_are_not_toc_pages(monkeypatch):
    assert score_toc_text("GENERAL\n1. 0. 1\nA requirement\n1. 0. 2\nAnother requirement\n1. 0. 3\nA third requirement")[1] < 40
    def unexpected(*args, **kwargs):
        pytest.fail("正常正文列表不应被强制 OCR")
    monkeypatch.setattr("pdf_enhance_core.ocr_engine.ocr_pdf_page", unexpected)
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 80), "CONTENTS")
        page.insert_text((60, 120), "1 ALPHA ....... (1)")
        page.insert_text((60, 160), "2 BETA ....... (2)")
        page = doc.new_page()
        for y, text in ((80, "EXAMPLES"), (120, "1"), (160, "2"), (200, "3"), (240, "4"), (280, "5")):
            page.insert_text((60, y), text)
        assert detect_toc_pages_with_ocr(doc, prefer_text=True) == [1]


def test_mixed_toc_pages_are_combined_without_dropping_entries(tmp_path, monkeypatch):
    source = tmp_path / "mixed.pdf"
    with fitz.open() as doc:
        for _ in range(5):
            doc.new_page(width=600, height=800)
        for y, text in ((60, "CONTENTS"), (100, "1 ALPHA ....... (1)"), (150, "1.1 DETAILS ....... (1)")):
            doc[0].insert_text((60, y), text)
        doc[2].insert_text((60, 100), "1 ALPHA")
        doc[2].insert_text((60, 300), "1.1 DETAILS")
        doc[3].insert_text((60, 100), "2 BETA")
        doc[4].insert_text((60, 100), "3 GAMMA")
        doc.save(source)
    calls = []
    def fake_ocr(page, dpi):
        calls.append(page.number)
        assert page.number == 1
        return [ocr_item("CONTENTS", 200, 50), ocr_item("2 BETA"), ocr_item("(2)", 500),
                ocr_item("3 GAMMA", y=150), ocr_item("(3)", 500, 150)], (600, 800)
    monkeypatch.setattr("pdf_enhance_core.ocr_engine.ocr_pdf_page", fake_ocr)
    monkeypatch.setattr("pdf_enhance_core.parser._ocr_left_numbering_tokens", lambda *args: [])
    monkeypatch.setattr("pdf_enhance_core.parser._ocr_right_margin_page_numbers", lambda *args, **kwargs: [(107.5, 2), (157.5, 3)])
    result = app_without_window().process_worker(source, gui.MODES[2], tmp_path / "output.pdf", 1)
    assert result[0] == "analysis"
    assert result[3] == [1, 2]
    assert [entry.title for entry in result[2]] == ["1 ALPHA", "1.1 DETAILS", "2 BETA", "3 GAMMA"]
    assert result[4] == 2
    assert calls == [1]


def test_text_extraction_failure_is_treated_as_unusable():
    class BrokenPage:
        def get_text(self):
            raise RuntimeError("damaged font mapping")
    assert usable_page_text(BrokenPage()) == ""


def test_native_toc_parser_failure_retries_only_that_page(tmp_path, monkeypatch):
    source = tmp_path / "book.pdf"
    create_book(source)
    original = gui.parse_toc_from_pages
    def fail_native(*args):
        raise RuntimeError("unsupported native text layout")
    calls = []
    def fallback(doc, pages, **kwargs):
        calls.extend(pages)
        return original(doc, pages)
    monkeypatch.setattr(gui, "parse_toc_from_pages", fail_native)
    monkeypatch.setattr(gui, "parse_toc_from_ocr_pages", fallback)
    result = app_without_window().process_worker(source, gui.MODES[2], tmp_path / "output.pdf", 1)
    assert result[0] == "analysis" and len(result[2]) == 3
    assert calls == [1]


def test_excerpt_uses_negative_offset_and_keeps_logical_page_digits(tmp_path, monkeypatch):
    from pdf_enhance_core.parser import _ocr_right_margin_page_numbers
    monkeypatch.setattr("pdf_enhance_core.ocr_engine.get_ocr_engine", lambda: (
        lambda *args, **kwargs: ([[[[10, 100], [30, 100], [30, 120], [10, 120]], "(166)", .99]], None)))
    with fitz.open() as doc:
        doc.new_page(width=600, height=800)
        doc.new_page(width=600, height=800).insert_text((60, 100), "1 ALPHA")
        assert _ocr_right_margin_page_numbers(doc[0])[0][1] == 166
        items = [TOCItem("1 ALPHA", 166)]
        offset, _ = detect_page_offset(doc, items, 1, require_match=True)
        assert offset == -164
        offset, _ = detect_page_offset_with_ocr(doc, items, 1, ocr_cache={1: [ocr_item("1 ALPHA")]}, require_match=True)
        assert offset == -164
        source = tmp_path / "excerpt.pdf"
        doc.save(source)
    from pdf_enhance_core.bookmark_writer import apply_bookmarks
    output = tmp_path / "excerpt_bookmark.pdf"
    assert apply_bookmarks(str(source), str(output), items, page_offset=offset)[0]
    with fitz.open(output) as doc:
        assert doc.get_toc()[0][2] == 2


def test_offset_without_match_does_not_silently_guess():
    with fitz.open() as doc:
        doc.new_page()
        doc.new_page().insert_text((60, 100), "No matching heading")
        items = [TOCItem("1 ALPHA", 1)]
        with pytest.raises(ValueError, match="无法自动确定"):
            detect_page_offset(doc, items, 1, require_match=True)
        with pytest.raises(ValueError, match="无法自动确定"):
            detect_page_offset_with_ocr(doc, items, 1, ocr_cache={1: [ocr_item("No matching heading")]}, require_match=True)


def test_offset_conflicting_votes_are_rejected():
    with fitz.open() as doc:
        doc.new_page()
        doc.new_page().insert_text((60, 100), "1 ALPHA")
        doc.new_page().insert_text((60, 100), "2 BETA")
        items = [TOCItem("1 ALPHA", 1), TOCItem("2 BETA", 1)]
        with pytest.raises(ValueError, match="不一致"):
            detect_page_offset(doc, items, 1, require_match=True)
