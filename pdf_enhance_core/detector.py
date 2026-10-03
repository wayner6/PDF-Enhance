import re
import unicodedata
from typing import List, Tuple, Optional
import pymupdf as fitz

TOC_KEYWORDS = [
    "目录", "目  录", "目 录", "contents", "table of contents", 
    "content", "index", "目次", "总目"
]

def text_layer_stats(doc: fitz.Document, min_chars_per_page: int = 10) -> Tuple[int, int]:
    """返回具有一定文字量的页数和总页数；这不等同于文字层质量良好。"""
    pages_with_text = sum(
        1 for page in doc
        if len(page.get_text().strip()) >= min_chars_per_page
    )
    return pages_with_text, len(doc)


def text_quality_stats(doc: fitz.Document, min_chars_per_page: int = 20) -> Tuple[int, int]:
    """统计疑似乱码文字层页数。控制字符、私用区字符和替换字符通常表示字体映射损坏。"""
    suspicious_pages = 0
    evaluated_pages = 0
    for page in doc:
        text = page.get_text().strip()
        if len(text) < min_chars_per_page:
            continue
        evaluated_pages += 1
        length = max(len(text), 1)
        control_count = sum(
            unicodedata.category(char) == "Cc" and char not in "\n\r\t"
            for char in text
        )
        private_count = sum(unicodedata.category(char) == "Co" for char in text)
        replacement_count = text.count("�")
        if (
            control_count / length >= 0.015
            or private_count / length >= 0.01
            or replacement_count / length >= 0.005
        ):
            suspicious_pages += 1
    return suspicious_pages, evaluated_pages


def scan_layer_stats(doc: fitz.Document) -> Tuple[int, int]:
    """返回全页扫描图页数，以及含不可见文字的页数。"""
    scan_like_pages = 0
    invisible_text_pages = 0
    for page in doc:
        page_area = max(page.rect.get_area(), 1.0)
        image_coverage = max(
            (
                fitz.Rect(info["bbox"]).get_area() / page_area
                for info in page.get_image_info()
                if info.get("bbox")
            ),
            default=0.0,
        )
        if image_coverage >= 0.8:
            scan_like_pages += 1
        if any(trace.get("type") == 3 for trace in page.get_texttrace()):
            invisible_text_pages += 1
    return scan_like_pages, invisible_text_pages


def check_pdf_text_layer(doc: fitz.Document, sample_pages: int = 10) -> bool:
    """
    检查 PDF 是否包含可抽取的文字层。
    采样前 N 页，如果总字数极少，则判定为纯图片扫描件。
    """
    total_chars = 0
    pages_to_check = min(sample_pages, len(doc))
    for i in range(pages_to_check):
        text = doc[i].get_text().strip()
        total_chars += len(text)
    
    # 平均每页少于 10 个字符则认为无文本层
    return total_chars >= 20

def score_toc_text(text: str) -> Tuple[bool, int]:
    """根据一页的文本内容判断它是否像目录页。"""
    if not text:
        return False, 0

    score = 0
    
    # 提取有效行（过滤空行）
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) < 2:
        return False, 0
        
    has_keyword = False
    # 1. 关键词命中（整行包含或前缀包含"目录"/"contents"）
    for line in lines[:5]:  # 关键词一般在页首前几行
        line_clean = re.sub(r'\s+', '', line.lower())
        for kw in ["目录", "目次", "contents", "tableofcontents", "总目"]:
            if kw == line_clean or line_clean.startswith(kw):
                score += 50
                has_keyword = True
                break
        if has_keyword:
            break
            
    # 2. 结构特征：检查带有点引线或多行带页码的条目
    numbered_lines = 0
    dot_leader_lines = 0
    
    for line in lines:
        # 检查是否包含点引线 (如 .......)
        if re.search(r'[.·…\-_﹍]{3,}', line):
            dot_leader_lines += 1
        # 检查行尾是否有合理页码数字 (通常 1~4 位数字，避免误把年份如 2025 当页码)
        m = re.search(r'(?:[.·…\-_﹍\s]|^)(\d{1,4})\s*$', line)
        if m:
            numbered_lines += 1
            
    # 目录页必须有足够的条目数（至少 3 行以上带有页码，或者有关键词且至少 2 行带页码）
    if numbered_lines >= 3:
        score += min(50, numbered_lines * 10)
    elif has_keyword and numbered_lines >= 1:
        score += 20
    else:
        # 条目太少，不视为目录页
        score = 0

    if dot_leader_lines >= 2:
        score += 30
        
    return score >= 60, score


def is_toc_page(page: fitz.Page) -> Tuple[bool, int]:
    """分析带文字层页面的目录特征。"""
    return score_toc_text(page.get_text())


def _best_contiguous_group(detected_pages: List[int]) -> List[int]:
    if not detected_pages:
        return []
    grouped = []
    current_group = [detected_pages[0]]
    for page_number in detected_pages[1:]:
        if page_number - current_group[-1] <= 2:
            current_group.append(page_number)
        else:
            grouped.append(current_group)
            current_group = [page_number]
    grouped.append(current_group)
    best_group = max(grouped, key=len)
    return list(range(best_group[0], best_group[-1] + 1))


def detect_toc_pages(doc: fitz.Document, max_search_pages: int = 35) -> List[int]:
    """
    在前 max_search_pages 页中自动探测连续的目录页码（1-based 物理页码）。
    """
    search_limit = min(max_search_pages, len(doc))
    detected_pages = []
    
    for i in range(search_limit):
        page = doc[i]
        is_toc, score = is_toc_page(page)
        if is_toc:
            detected_pages.append(i + 1)
            
    return _best_contiguous_group(detected_pages)


def detect_toc_pages_with_ocr(
    doc: fitz.Document,
    max_search_pages: int = 35,
    dpi: int = 150,
) -> List[int]:
    """OCR 探测目录，并把相邻的中文目录和英文目录分成不同组。"""
    from .ocr_engine import ocr_pdf_page

    candidates = []
    search_limit = min(max_search_pages, len(doc))
    for page_index in range(search_limit):
        items, _ = ocr_pdf_page(doc[page_index], dpi=dpi)
        text = "\n".join(str(item.get("text", "")) for item in items)
        is_toc, score = score_toc_text(text)
        if score < 40:
            continue
        cjk_count = sum("\u4e00" <= char <= "\u9fff" for char in text)
        latin_count = sum(char.isascii() and char.isalpha() for char in text)
        language = "zh" if cjk_count >= max(5, latin_count // 5) else "en"
        candidates.append({
            "page": page_index + 1,
            "language": language,
            "score": score,
            "strong": is_toc,
        })

    groups = []
    for candidate in candidates:
        if (
            groups
            and candidate["page"] == groups[-1][-1]["page"] + 1
            and candidate["language"] == groups[-1][-1]["language"]
        ):
            groups[-1].append(candidate)
        else:
            groups.append([candidate])

    groups = [group for group in groups if any(row["strong"] for row in group)]
    if not groups:
        return []
    best = max(
        groups,
        key=lambda group: (
            len(group),
            sum(row["score"] for row in group),
            -group[0]["page"],
        ),
    )
    return [row["page"] for row in best]


def parse_page_range(range_str: str, max_page: int) -> Optional[List[int]]:
    """
    解析用户输入的页码范围，如 '5-8', '5,6,7', '5'
    """
    range_str = range_str.strip()
    if not range_str:
        return None
        
    pages = set()
    parts = range_str.replace('，', ',').split(',')
    
    try:
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if '-' in part:
                s, e = part.split('-', 1)
                s_num, e_num = int(s.strip()), int(e.strip())
                if s_num > e_num or s_num < 1 or e_num > max_page:
                    return None
                pages.update(range(s_num, e_num + 1))
            else:
                num = int(part)
                if num < 1 or num > max_page:
                    return None
                pages.add(num)
        return sorted(list(pages))
    except ValueError:
        return None
