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
def test_window_modes_and_busy_controls(tmp_path, monkeypatch):
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
        assert not hasattr(app, "bookmarks")
        assert not hasattr(app, "toc_text")
        assert not hasattr(app, "save_btn")
        for mode in gui.MODES:
            app.mode_var.set(mode)
            app.mode_changed()
            assert app.start_btn.cget("text") == "开始处理"
        app.set_busy(True)
        assert str(app.start_btn.cget("state")) == "disabled"
        assert str(app.cancel_btn.cget("state")) == "normal"
        app.cancel_job()
        assert app.cancel_event.is_set()
        assert str(app.cancel_btn.cget("state")) == "disabled"
        assert "正在中断" in app.status_var.get()
        app.set_busy(False)
        assert str(app.start_btn.cget("state")) == "normal"
        assert str(app.cancel_btn.cget("state")) == "disabled"
        root.deiconify()
        root.update()
        assert (root.winfo_width(), root.winfo_height()) == root.minsize()
        assert root.winfo_width() < round(620 * root.winfo_fpixels("1i") / 96)
        source = tmp_path / "source.pdf"
        source.write_bytes(b"test input")
        app.source = source
        app.directory_var.set(str(tmp_path))
        jobs = []
        monkeypatch.setattr(app, "start_job", lambda target, *args: jobs.append((target, args)))
        app.start()
        assert jobs[0][0] == app.complete_worker
        output = tmp_path / "source_bookmark.pdf"
        output.write_bytes(b"existing")
        monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args: False)
        app.start()
        assert len(jobs) == 1
        assert output.read_bytes() == b"existing"
    finally:
        app.workspace.cleanup()
        root.destroy()
