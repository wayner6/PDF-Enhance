import re
from typing import List, Tuple
from collections import Counter
import pymupdf as fitz
from .parser import TOCItem

def normalize_text_for_search(text: str) -> str:
    """去除非字母数字和汉字的干扰字符，便于容错匹配"""
    return re.sub(r'[\s\.\、\:\：\-\_\(\)（）\[\]【】\·]+', '', text).lower()

def detect_page_offset(
    doc: fitz.Document, 
    toc_items: List[TOCItem], 
    last_toc_page: int, 
    search_limit: int = 50
) -> Tuple[int, str]:
    """
    通过在目录页之后的正文页中搜索前几个章节标题，自动推算物理页偏移量。
    返回 (offset, reason_description)
    """
    if not toc_items:
        return 0, "未提供目录条目，默认偏移量 0"

    # 挑选前几个候选条目（优先选取阿拉伯数字正文章节，如 "1 总则", "第一章"）
    candidate_items = []
    for item in toc_items:
        clean_title = item.title.strip()
        # 跳过目次、目录、前言等前置非正文章节，寻找真正的第一章/正文第一条
        if clean_title in ["目次", "目录", "前言", "序言"]:
            continue
        if len(clean_title) >= 2 and item.logical_page <= 30:
            candidate_items.append(item)
            if len(candidate_items) >= 6:
                break
            
    if not candidate_items:
        candidate_items = toc_items[:3]
            
    if not candidate_items:
        candidate_items = toc_items[:3]

    start_search_pno = last_toc_page + 1
    end_search_pno = min(len(doc), last_toc_page + search_limit)

    offset_votes = []
    match_logs = []

    for item in candidate_items:
        target_norm = normalize_text_for_search(item.title)
        if len(target_norm) < 2:
            continue

        # 在后续正文页面中寻找
        for pno in range(start_search_pno, end_search_pno + 1):
            page_text = doc[pno - 1].get_text()
            page_norm = normalize_text_for_search(page_text)
            
            # 如果正文页包含了该条目的标题
            if target_norm in page_norm:
                computed_offset = pno - item.logical_page
                offset_votes.append(computed_offset)
                match_logs.append(f"条目【{item.title}】(逻辑页 {item.logical_page}) 在第 {pno} 页命中 -> 偏移量: {computed_offset}")
                break  # 找到首次出现即停止该条目搜索

    if offset_votes:
        # 取出现频次最多的偏移量
        counter = Counter(offset_votes)
        best_offset, count = counter.most_common(1)[0]
        desc = f"找到 {len(offset_votes)} 个章节标题，页码偏移 {best_offset:+}（{count}/{len(offset_votes)} 个结果一致）。"
        return best_offset, desc

    # 兜底推算：假设正文第 1 页刚好紧跟在目录后
    first_item = toc_items[0]
    fallback_offset = max(0, (last_toc_page + 1) - first_item.logical_page)
    desc = f"未能在正文快速检索到标题，根据目录结束位置推测偏移量为 {fallback_offset:+}"
    return fallback_offset, desc


def detect_page_offset_with_ocr(
    doc: fitz.Document,
    toc_items: List[TOCItem],
    last_toc_page: int,
    dpi: int = 150,
    radius: int = 4,
    ocr_cache: dict | None = None,
) -> Tuple[int, str]:
    """
    为没有全文文字层的扫描 PDF 推算偏移量。

    先根据目录结束位置得到粗略偏移，再只 OCR 各候选章节预期页附近的小范围页面，
    避免为了制作书签而识别整本书。
    """
    from .ocr_engine import cached_page_ocr

    candidates = [
        item for item in toc_items
        if item.title.strip() not in {"目次", "目录", "前言", "序言"}
        and item.logical_page >= 1
    ][:6]
    if not candidates:
        return 0, "没有可用于推算偏移量的正文章节"

    first_logical = min(item.logical_page for item in candidates)
    rough_offset = max(0, last_toc_page + 1 - first_logical)
    page_cache = {}
    votes = []

    for item in candidates:
        expected = item.logical_page + rough_offset
        start = max(last_toc_page + 1, expected - radius)
        end = min(len(doc), expected + radius)
        target = normalize_text_for_search(item.title)
        core = re.sub(r"^(?:第[^章节]{1,8}[章节]|\d+(?:\.\d+)*)", "", target)
        probes = [probe for probe in (target, core, core[:8]) if len(probe) >= 3]

        for physical_page in range(start, end + 1):
            if physical_page not in page_cache:
                items, _ = cached_page_ocr(doc[physical_page - 1], dpi, ocr_cache)
                page_cache[physical_page] = normalize_text_for_search(
                    "\n".join(str(row.get("text", "")) for row in items)
                )
            page_text = page_cache[physical_page]
            if any(probe in page_text for probe in probes):
                votes.append(physical_page - item.logical_page)
                break

    if votes:
        counter = Counter(votes)
        best_offset, count = counter.most_common(1)[0]
        return best_offset, (
            f"找到 {len(votes)} 个章节标题，页码偏移 {best_offset:+} "
            f"（{count}/{len(votes)} 个结果一致）。"
        )

    return rough_offset, (
        f"局部 OCR 未匹配到章节标题，根据目录结束位置估算偏移量为 {rough_offset:+}；"
        "请在生成前人工确认。"
    )
