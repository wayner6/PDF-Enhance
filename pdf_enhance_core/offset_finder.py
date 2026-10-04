import re
from collections import Counter
from typing import List, Tuple

import pymupdf as fitz

from .bookmark_writer import find_title_coordinate_on_page, find_title_coordinate_from_ocr_items
from .detector import usable_page_text
from .parser import TOCItem


def normalize_text_for_search(text: str) -> str:
    return re.sub(r'[\s\.\、\:\：\-\_\(\)（）\[\]【】\·]+', '', text).lower()


def _offset_candidates(toc_items):
    return [item for item in toc_items
            if item.title.strip() not in {"目次", "目录", "前言", "序言", "编制说明"}
            and item.logical_page >= 1][:6]


def _offset_result(votes, fallback, require_match):
    if votes:
        best, count = Counter(votes).most_common(1)[0]
        if require_match and count * 2 <= len(votes):
            raise ValueError("章节页码匹配结果不一致，无法可靠确定书签页码")
        return best, f"找到 {len(votes)} 个章节标题，页码偏移 {best:+}（{count}/{len(votes)} 个结果一致）。"
    if require_match:
        raise ValueError("未匹配到正文章节标题，无法自动确定书签页码")
    return fallback, f"未匹配到章节标题，根据目录结束位置估算偏移量为 {fallback:+}；请人工确认。"


def detect_page_offset(
    doc: fitz.Document,
    toc_items: List[TOCItem],
    last_toc_page: int,
    search_limit: int = 50,
    require_match: bool = False,
) -> Tuple[int, str]:
    """优先利用正常文字层，以完整标题而非正文中的同名词语进行投票。"""
    candidates = _offset_candidates(toc_items)
    fallback = last_toc_page + 1 - min((item.logical_page for item in candidates), default=1)
    votes = []
    for item in candidates:
        for physical_page in range(last_toc_page + 1, min(len(doc), last_toc_page + search_limit) + 1):
            page = doc[physical_page - 1]
            if usable_page_text(page) and find_title_coordinate_on_page(page, item.title) is not None:
                votes.append(physical_page - item.logical_page)
                break
    return _offset_result(votes, fallback, require_match)


def detect_page_offset_with_ocr(
    doc: fitz.Document,
    toc_items: List[TOCItem],
    last_toc_page: int,
    dpi: int = 150,
    radius: int = 4,
    ocr_cache: dict | None = None,
    require_match: bool = False,
) -> Tuple[int, str]:
    """文字层匹配失败后，复用已有 OCR 并识别章节预计位置附近的页面。"""
    from .ocr_engine import cached_page_ocr

    candidates = _offset_candidates(toc_items)
    # 允许节选 PDF 的逻辑页码大于物理页码，不强制将偏移截为非负数。
    rough = last_toc_page + 1 - min((item.logical_page for item in candidates), default=1)
    cache = ocr_cache if ocr_cache is not None else {}
    votes = []
    for item in candidates:
        expected = item.logical_page + rough
        nearby = range(max(last_toc_page + 1, expected - radius), min(len(doc), expected + radius) + 1)
        # 探测目录或全文 OCR 时已识别的正文页，无须受小半径限制。
        known = sorted(index + 1 for index in cache if last_toc_page <= index < len(doc))
        matched = False
        for physical_page in known:
            if find_title_coordinate_from_ocr_items(cache[physical_page - 1], item.title) is not None:
                votes.append(physical_page - item.logical_page)
                matched = True
                break
        if matched:
            continue
        for physical_page in nearby:
            if physical_page - 1 in cache:
                continue
            items, _ = cached_page_ocr(doc[physical_page - 1], dpi, cache)
            if find_title_coordinate_from_ocr_items(items, item.title) is not None:
                votes.append(physical_page - item.logical_page)
                break
    return _offset_result(votes, rough, require_match)
