from pdf_enhance_core.bookmark_writer import find_title_coordinate_from_ocr_items


def _item(text, x0, y0, x1, y1):
    return {
        "text": text,
        "box": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
    }


def test_numbered_title_does_not_match_earlier_body_text():
    items = [
        _item("完成定期检查后应形成报告", 80, 200, 300, 215),
        _item("5.2定期检查", 60, 500, 150, 515),
    ]
    point = find_title_coordinate_from_ocr_items(items, "5.2 定期检查")
    assert point.x == 60
    assert point.y == 485


def test_split_number_and_title_boxes_are_matched_as_one_heading():
    items = [
        _item("技术状况评价应符合要求", 80, 200, 300, 215),
        _item("7.2", 60, 350, 90, 365),
        _item("技术状况评价", 95, 351, 180, 366),
    ]
    point = find_title_coordinate_from_ocr_items(items, "7.2 技术状况评价")
    assert point.x == 60
    assert point.y == 335
