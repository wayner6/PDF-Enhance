"""Cancellation must stop work and never replace an existing output."""

import multiprocessing
from pathlib import Path
import queue
import tempfile
import threading
import time

import pymupdf as fitz
import pytest

import pdf_enhance_gui as gui
from pdf_enhance_core import TOCItem, apply_bookmarks
from pdf_enhance_core.cancellation import ProcessingCancelled, cancellation_scope, check_cancelled
import pdf_enhance_core.pdf_ocr_pipeline as pipeline


def make_pdf(path):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "1 ALPHA - searchable document text")
        doc.save(path)


def noop_initializer():
    pass


def slow_ocr(args):
    Path(args[0]).with_suffix(".started").write_text("started", encoding="utf-8")
    time.sleep(60)
    return args[1], [], (612.0, 792.0), None


def test_cancel_context_is_reset_and_task_local():
    event = threading.Event()
    event.set()
    with pytest.raises(ProcessingCancelled):
        with cancellation_scope(event):
            pytest.fail("A cancelled task must not start")
    check_cancelled()
    with cancellation_scope(threading.Event()):
        check_cancelled()


@pytest.mark.parametrize("new_output", [False, True])
def test_bookmark_cancel_during_save_is_atomic(tmp_path, monkeypatch, new_output):
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    make_pdf(source)
    original = source.read_bytes()
    if not new_output:
        output.write_bytes(b"existing output")
    event = threading.Event()
    save = fitz.Document.save
    def cancel_after_save(doc, *args, **kwargs):
        save(doc, *args, **kwargs)
        event.set()
    monkeypatch.setattr(fitz.Document, "save", cancel_after_save)
    with pytest.raises(ProcessingCancelled), cancellation_scope(event):
        apply_bookmarks(str(source), str(output), [TOCItem("1 ALPHA", 1)])
    assert source.read_bytes() == original
    if new_output:
        assert not output.exists()
    else:
        assert output.read_bytes() == b"existing output"
    assert not list(tmp_path.glob(".pdf-enhance-*.pdf"))


def test_ocr_cancel_terminates_waiting_workers(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    make_pdf(source)
    original = source.read_bytes()
    output.write_bytes(b"existing output")
    event = threading.Event()
    results = []
    baseline = {child.pid for child in multiprocessing.active_children()}
    monkeypatch.setattr(pipeline, "worker_ocr_init", noop_initializer)
    monkeypatch.setattr(pipeline, "worker_ocr_page_task", slow_ocr)
    def run():
        try:
            with cancellation_scope(event):
                pipeline.generate_searchable_pdf(str(source), str(output), max_workers=1,
                                                 skip_pages_with_text=False)
        except ProcessingCancelled:
            results.append("cancelled")
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not source.with_suffix(".started").exists() and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    started = source.with_suffix(".started").exists()
    event.set()
    thread.join(15)
    assert started
    assert not thread.is_alive(), "Cancellation must not wait for a 60-second OCR task"
    assert results == ["cancelled"]
    assert {child.pid for child in multiprocessing.active_children()} == baseline
    assert source.read_bytes() == original
    assert output.read_bytes() == b"existing output"


def test_ocr_cancel_during_text_injection_preserves_output(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    make_pdf(source)
    output.write_bytes(b"existing output")
    event = threading.Event()
    def cancel_injection(*args):
        event.set()
    monkeypatch.setattr(pipeline, "inject_invisible_text_layer", cancel_injection)
    with pytest.raises(ProcessingCancelled), cancellation_scope(event):
        pipeline.generate_searchable_pdf(str(source), str(output), max_workers=1)
    assert output.read_bytes() == b"existing output"
    assert not list(tmp_path.glob(".pdf-enhance-*.pdf"))


def test_ocr_fallback_cancel_is_atomic(tmp_path, monkeypatch):
    working = tmp_path / "searchable.pdf"
    output = tmp_path / "output.pdf"
    working.write_bytes(b"completed OCR")
    output.write_bytes(b"existing output")
    event = threading.Event()
    copy = gui.shutil.copyfile
    def cancel_after_copy(*args):
        copy(*args)
        event.set()
    monkeypatch.setattr(gui.shutil, "copyfile", cancel_after_copy)
    with pytest.raises(ProcessingCancelled), cancellation_scope(event):
        gui.export_ocr_fallback(working, output)
    assert output.read_bytes() == b"existing output"
    assert working.read_bytes() == b"completed OCR"
    assert not list(tmp_path.glob(".pdf-enhance-*.pdf"))


def test_gui_cancel_is_not_an_error_or_ocr_fallback(tmp_path):
    app = gui.PDFEnhanceApp.__new__(gui.PDFEnhanceApp)
    app.events = queue.Queue()
    app.cancel_event = threading.Event()
    app.source = tmp_path / "original.pdf"
    app.source.write_bytes(b"original PDF")
    with tempfile.TemporaryDirectory(dir=tmp_path) as folder:
        app.workspace = type("Workspace", (), {"name": folder})()
        working = Path(folder) / "searchable.pdf"
        working.write_bytes(b"intermediate OCR")
        def cancelled_bookmarks():
            app.cancel_event.set()
            check_cancelled()
        app.run_job(cancelled_bookmarks)
        kind, message = app.events.get_nowait()
        assert kind == "cancelled" and "已中断" in message
        assert not working.exists()
        assert app.source.read_bytes() == b"original PDF"
        assert app.events.empty()
        app.cancel_event.clear()
        app.run_job(lambda: ("saved", "next task completed"))
        assert app.events.get_nowait() == ("done", ("saved", "next task completed"))
