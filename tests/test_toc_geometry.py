import pymupdf as fitz

from pdf_enhance_core.detector import detect_toc_pages_with_ocr
from pdf_enhance_core.parser import _rebuild_ocr_title_rows


def _item(text, x0, y0, x1, y1):
    return {
        "text": text,
        "box": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
    }


def test_split_number_and_title_are_rebuilt_as_one_row():
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    rows = _rebuild_ocr_title_rows(page, [
        _item("1", 60, 100, 70, 120),
        _item("总则", 90, 101, 140, 121),
        _item("1", 510, 100, 520, 120),
        _item("2.1", 80, 140, 110, 160),
        _item("术语", 125, 140, 175, 160),
        _item("2", 510, 140, 520, 160),
    ])
    assert [text for _, text in rows] == ["1 总则", "2.1 术语"]
    document.close()


def test_chinese_and_english_contents_are_separate_groups(monkeypatch):
    texts = {
        5: "目录\n1 总则\n1\n2 术语\n2\n3 基本规定\n4",
        6: "4 监测方法\n12\n5 高层结构\n22\n6 桥梁结构\n33\n7 附录\n42",
        7: "Contents\n1 General\n1\n2 Terms\n2\n3 Requirements\n4",
        8: "4 Monitoring\n12\n5 Building\n22\n6 Bridge\n33\n7 Appendix\n42",
    }

    def fake_ocr(page, dpi=150):
        text = texts.get(page.number, "")
        return ([{"text": line} for line in text.splitlines()], (600, 800))

    monkeypatch.setattr("pdf_enhance_core.ocr_engine.ocr_pdf_page", fake_ocr)
    document = fitz.open()
    for _ in range(10):
        document.new_page()
    assert detect_toc_pages_with_ocr(document, max_search_pages=10) == [6, 7]
    document.close()
