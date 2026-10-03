import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf as fitz

from pdf_enhance_core.pdf_ocr_pipeline import inject_invisible_text_layer


def test_character_boxes_survive_leaked_page_matrix():
    """验证原内容流存在缩放矩阵时，隐藏文字仍按标准页面坐标写入。"""
    source = fitz.open()
    page = source.new_page(width=595.32, height=841.92)
    # 模拟扫描 PDF 常见的未隔离 CTM。没有 wrap_contents() 时，后续文字会被 0.75 缩放并错位。
    xref = source.get_new_xref()
    source.update_object(xref, "<< /Length 0 >>")
    source.update_stream(xref, b"0.75 0 0 -0.75 0 841.92 cm\n")
    page.set_contents(xref)

    output = fitz.open()
    output.insert_pdf(source)
    out_page = output[0]
    out_page.wrap_contents()

    ocr_items = [{
        "text": "固定",
        "box": [[100, 120], [140, 120], [140, 140], [100, 140]],
        "score": 1.0,
        "word_boxes": [
            [[100, 120], [118, 120], [118, 140], [100, 140]],
            [[121, 120], [139, 120], [139, 140], [121, 140]],
        ],
        "word_contents": ["固", "定"],
        "word_scores": [1.0, 1.0],
    }]
    inject_invisible_text_layer(out_page, ocr_items)

    with tempfile.TemporaryDirectory() as directory:
        temp_path = Path(directory) / "alignment.pdf"
        output.save(temp_path)
        output.close()
        source.close()

        with fitz.open(temp_path) as check:
            rects = check[0].search_for("固定")

    assert rects, "应能搜索到完整词语"
    assert abs(rects[0].x0 - 100) < 0.5
    assert abs(rects[0].y0 - 120) < 2.0


if __name__ == "__main__":
    test_character_boxes_survive_leaked_page_matrix()
    print("文字层坐标隔离与字符框对齐测试通过")
