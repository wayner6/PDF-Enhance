"""桌面任务路由回归检查；不依赖真实 OCR 识别率。"""

import os
import queue
import tkinter as tk
from pathlib import Path

import pymupdf as fitz
import pytest

import pdf_enhance_gui as gui
from pdf_enhance_core import TOCItem


@pytest.mark.parametrize("mode", gui.MODES)
def test_three_task_routes(tmp_path, monkeypatch, mode):
    source = tmp_path / "source.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((50, 50), "An ordinary PDF page with sufficient text")
        doc.save(source)
    calls = []

    def fake_ocr(source, output, **kwargs):
        calls.append(kwargs)
        Path(output).write_bytes(Path(source).read_bytes())
        return True, "OCR complete"

    monkeypatch.setattr(gui, "generate_searchable_pdf", fake_ocr)
    monkeypatch.setattr(gui, "detect_toc_pages_with_ocr", lambda *args, **kwargs: [1])
    monkeypatch.setattr(gui, "parse_toc_from_pages", lambda doc, pages: [TOCItem("1 Test", 1)])
    monkeypatch.setattr(gui, "detect_page_offset", lambda *args, **kwargs: (0, "offset found"))
    app = gui.PDFEnhanceApp.__new__(gui.PDFEnhanceApp)
    app.events = queue.Queue()
    target = gui.output_path(source, tmp_path, mode)
    result = app.process_worker(source, mode, target, 2)
    if mode == gui.MODES[0]:
        assert result[0] == "saved"
    else:
        assert result[0] == "analysis"
        assert result[2][0].title == "1 Test"
    if mode == gui.MODES[2]:
        assert not calls
        assert result[1] == source
    else:
        assert calls[0]["max_workers"] == 2
        assert calls[0]["skip_pages_with_text"] is False
        assert calls[0]["replace_existing_scan_text"] is True
        assert target.exists()


def test_output_names(tmp_path):
    assert [gui.output_path("book.pdf", tmp_path, mode).name for mode in gui.MODES] == [
        "book_ocr.pdf", "book_ocr_bookmark.pdf", "book_bookmark.pdf"
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows desktop layout smoke check")
def test_window_modes_and_busy_controls():
    gui.enable_high_dpi()
    root = tk.Tk()
    root.withdraw()
    app = gui.PDFEnhanceApp(root)
    try:
        assert "PDF 增强" in root.title()
        assert len(gui.MODES) == 3
        assert not hasattr(app, "pages_var")
        assert not hasattr(app, "offset_var")
        assert not hasattr(app, "target_label")
        assert not hasattr(app, "description_label")
        app.mode_var.set(gui.MODES[0])
        app.mode_changed()
        assert not app.bookmarks.winfo_manager()
        assert app.start_btn.cget("text") == "开始全文 OCR"
        app.mode_var.set(gui.MODES[2])
        app.mode_changed()
        assert app.bookmarks.winfo_manager() == "grid"
        app.set_busy(True)
        assert str(app.start_btn.cget("state")) == "disabled"
        app.set_busy(False)
        assert str(app.start_btn.cget("state")) == "normal"
        assert str(app.save_btn.cget("state")) == "disabled"
        root.update_idletasks()
    finally:
        app.workspace.cleanup()
        root.destroy()
