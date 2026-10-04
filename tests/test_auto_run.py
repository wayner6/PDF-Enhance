"""桌面版三个任务均自动输出，不再等待目录校对。"""

import queue
from pathlib import Path

import pymupdf as fitz
import pytest

import pdf_enhance_gui as gui
from pdf_enhance_core import TOCItem
from tests.create_sample_pdf import generate_sample_pdf


@pytest.mark.parametrize("mode", gui.MODES)
def test_single_task_automatically_exports_pdf(tmp_path, monkeypatch, mode):
    source = tmp_path / "source.pdf"
    generate_sample_pdf(str(source))
    original = source.read_bytes()
    directory = tmp_path / "output"
    directory.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = gui.output_path(source, directory, mode)
    working = workspace / "searchable.pdf" if mode == gui.MODES[1] else output
    calls = []
    def fake_full_ocr(source, target, **kwargs):
        calls.append(kwargs)
        Path(target).write_bytes(Path(source).read_bytes())
        return True, "OCR complete"
    def unexpected_ocr(*args, **kwargs):
        pytest.fail("正常文字层不应因自动保存而被重新识别")
    monkeypatch.setattr(gui, "generate_searchable_pdf", fake_full_ocr)
    monkeypatch.setattr("pdf_enhance_core.ocr_engine.ocr_pdf_page", unexpected_ocr)
    app = gui.PDFEnhanceApp.__new__(gui.PDFEnhanceApp)
    app.events = queue.Queue()
    result = app.complete_worker(source, mode, working, output, 1)
    assert result[0] == "saved"  # 没有需要用户确认目录的 analysis 事件。
    assert source.read_bytes() == original
    assert [path.name for path in directory.iterdir()] == [output.name]
    assert bool(calls) == (mode != gui.MODES[2])
    with fitz.open(output) as doc:
        assert len(doc) == 9
        if mode != gui.MODES[0]:
            assert len(doc.get_toc()) == 5
            assert doc.get_toc()[0][2] == 5


@pytest.mark.parametrize("mode", [gui.MODES[1], gui.MODES[2]])
def test_auto_save_failure_preserves_combined_ocr_only(tmp_path, monkeypatch, mode):
    working = tmp_path / "searchable.pdf"
    working.write_bytes(b"completed OCR")
    output = tmp_path / "output.pdf"
    app = gui.PDFEnhanceApp.__new__(gui.PDFEnhanceApp)
    monkeypatch.setattr(app, "process_worker", lambda *args: (
        "analysis", working, [TOCItem("1 ALPHA", 1)], [1], 0, "", False, {}))
    def fail_save(*args):
        raise ValueError("书签目标超出范围")
    monkeypatch.setattr(app, "save_worker", fail_save)
    if mode == gui.MODES[1]:
        result = app.complete_worker(tmp_path / "source.pdf", mode, working, output, 1)
        assert result[0] == "ocr_only" and result[2] == working
        assert working.read_bytes() == b"completed OCR"
    else:
        with pytest.raises(ValueError, match="超出范围"):
            app.complete_worker(tmp_path / "source.pdf", mode, working, output, 1)
    assert not output.exists()
