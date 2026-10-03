import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf as fitz

from pdf_enhance_core import (
    apply_bookmarks,
    check_pdf_text_layer,
    detect_page_offset,
    detect_toc_pages,
    export_to_toc_file,
    import_from_toc_file,
    parse_toc_from_pages,
)
from tests.create_sample_pdf import generate_sample_pdf


def test_full_pipeline():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        sample_pdf = root / "sample_book.pdf"
        output_pdf = root / "sample_book_output.pdf"
        toc_txt = root / "toc.txt"
        generate_sample_pdf(str(sample_pdf))

        with fitz.open(sample_pdf) as doc:
            assert check_pdf_text_layer(doc) is True
            toc_pages = detect_toc_pages(doc)
            assert toc_pages == [4]

            items = parse_toc_from_pages(doc, toc_pages)
            assert len(items) == 5
            assert (items[0].title, items[0].level, items[0].logical_page) == (
                "第一章 计算机基础", 1, 1
            )
            assert (items[1].title, items[1].level, items[1].logical_page) == (
                "1.1 硬件体系架构", 2, 2
            )

            offset, _ = detect_page_offset(doc, items, toc_pages[-1])
            assert offset == 4

        export_to_toc_file(items, str(toc_txt))
        imported = import_from_toc_file(str(toc_txt))
        assert len(imported) == len(items)
        assert imported[1].title == items[1].title
        assert imported[1].level == items[1].level

        ok, count, message = apply_bookmarks(
            str(sample_pdf), str(output_pdf), items, page_offset=offset
        )
        assert ok, message
        assert count == 5

        with fitz.open(output_pdf) as output:
            actual_toc = output.get_toc()
            assert len(actual_toc) == 5
            assert actual_toc[0] == [1, "第一章 计算机基础", 5]
            assert actual_toc[1] == [2, "1.1 硬件体系架构", 6]


if __name__ == "__main__":
    test_full_pipeline()
    print("目录解析、偏移计算和书签写入测试通过")
