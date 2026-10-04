import io
import re
from typing import List, Optional, Tuple
from dataclasses import dataclass

import numpy as np
from PIL import Image
import pymupdf as fitz

@dataclass
class TOCItem:
    title: str
    logical_page: int
    level: int = 1
    raw_text: str = ""
    source_pdf_page: int = 1

def normalize_cjk_spaces(text: str) -> str:
    """去除 OCR 在中文字符之间误插入的多余空格"""
    text = re.sub(r'([\u4e00-\u9fa5])\s+([\u4e00-\u9fa5])', r'\1\2', text)
    text = re.sub(r'([\u4e00-\u9fa5])\s+([\u4e00-\u9fa5])', r'\1\2', text)
    text = re.sub(r'\s*([、，。；：！？（）《》“”])\s*', r'\1', text)
    # 规范化章节号与中文之间的空格 (如 '1总则' -> '1 总则')
    text = re.sub(
        r'^(\d{1,3}(?:\.\d+)*|第[零一二三四五六七八九十百\d]+[章节])\s*([A-Za-z\u4e00-\u9fa5])',
        r'\1 \2',
        text,
    )
    return text.strip()

def normalize_generic_ocr_title(title: str) -> str:
    """仅做与文档领域无关的目录标题规范化，不擅自替换具体词语。"""
    title = re.sub(r'目\s*次', '目次', title)
    title = re.sub(r'目\s*录', '目录', title)
    title = re.sub(r'^附录\s*([A-Za-z])\s*', r'附录 \1 ', title)
    return title.strip()

def clean_ocr_dot_leaders_noise(title: str) -> str:
    """清理中文标题末尾由 OCR 点引线误识别出的杂乱小写字母与符号"""
    title = re.sub(r'([\u4e00-\u9fa5）\)])[\s.·…\-_﹍\d]*[a-z\s\.\,\'\`\-_]{2,}$', r'\1', title)
    title = re.sub(r'[\s.·…\-_﹍:：\.\,\'\"\`~@#$%^&*+=]+$', '', title)
    title = re.sub(r'[\s.·…\-_﹍]+$', '', title)
    title = re.sub(r'^[\s.·…\-_﹍:：]+', '', title)
    return title.strip()

def clean_toc_title(raw_title: str) -> str:
    """全面清洗与修复标题"""
    title = clean_ocr_dot_leaders_noise(raw_title)
    title = normalize_cjk_spaces(title)
    title = clean_ocr_dot_leaders_noise(title)
    title = normalize_generic_ocr_title(title)
    return title.strip()

def is_meaningful_title(title: str) -> bool:
    """检查标题是否是有意义的真实标题，过滤掉由 OCR 引线误识别成的纯英文乱码"""
    clean = title.strip()
    if len(clean) < 2:
        return False
        
    if re.search(r'[\u4e00-\u9fa5]', clean):
        return True
        
    if re.search(r'\b(chapter|part|section|appendix|index|contents|annex)\b', clean, flags=re.IGNORECASE):
        return True
        
    if re.search(r'^[0-9\.\s]*[A-Z]{2,}', clean):
        return True
        
    return False

def is_ignorable_header_or_noise(text: str, total_pdf_pages: int = 100) -> bool:
    """检查是否是网站水印、页眉噪声或无意义行"""
    clean = re.sub(r'\s+', '', text).lower()
    if not clean:
        return True
        
    # 过滤常见网址、邮箱和下载页眉，不绑定任何具体网站或文档来源。
    if re.search(r"(?:https?://|www\.|[a-z0-9_-]+\.(?:com|cn|net|org)(?:\b|/))", clean):
        return True
    if any(keyword in clean for keyword in ("下载地址", "关注微信", "公众号")):
        return True
        
    if re.search(r'^(DL|GB|ISO|IEC|IEEE|GJB|Q/)[/\-_T\s\d—]+$', clean, flags=re.IGNORECASE):
        return True
        
    return False

def parse_roman_numeral(s: str) -> Optional[int]:
    """将罗马数字转换为整数"""
    roman_map = {'i': 1, 'v': 5, 'x': 10, 'l': 50, 'c': 100, 'd': 500, 'm': 1000}
    s = s.lower().strip()
    if not s or not all(c in roman_map for c in s):
        return None
    res = 0
    prev = 0
    for c in reversed(s):
        val = roman_map[c]
        if val < prev:
            res -= val
        else:
            res += val
        prev = val
    return res

def guess_level_by_numbering(title: str) -> Optional[int]:
    """根据标题编号规则猜测层级"""
    clean = normalize_cjk_spaces(title.strip())
    if re.match(r'^\d+\.\d+\.\d+(\.\d+)?', clean):
        dots = len(re.match(r'^[\d\.]+', clean).group(0).split('.')) - 1
        return min(dots + 1, 4)
    if re.match(r'^\d+\.\d+\b', clean):
        return 2
    if re.match(r'^(第[零一二三四五六七八九十百]+章|Chapter\s*\d+|\d+\s*[A-Za-z\u4e00-\u9fa5]|\d+[\.\、])', clean):
        return 1
    if re.match(r'^(第[零一二三四五六七八九十百]+节)', clean):
        return 2
    if re.match(r'^(附录|参考文献|致谢|前言|序|后记|索引|目次|目录|编制说明)', clean):
        return 1
    return None

def extract_items_from_row_text(row_text: str) -> List[Tuple[str, int]]:
    """
    使用精准 Tokenizer 从一行文本中扫描提取所有章节条目与页码。
    """
    if is_ignorable_header_or_noise(row_text):
        return []
    # 标准、规范常用带括号的目录页码，如“总则 …… (1)”。
    row_text = re.sub(r'[（(]\s*(\d{1,4}|[IVXLCDMivxlcdm]+)\s*[）)]\s*$', r' \1', row_text)

    # 以章节号开头且带点引线的目录行按整行解析，避免把 SF6 中的“6”
    # 误当成第二个章节号。
    if re.match(r"^\s*\d{1,3}(?:\.\d+)*\s+", row_text) and re.search(
        r"[.·…_﹍]{3,}", row_text
    ):
        whole_line = re.search(
            r"^(.*?)(?:[.·…\-_﹍\s]{2,})(\d{1,4}|[IVXLCDMivxlcdm]+)\s*$",
            row_text,
        )
        if whole_line:
            title = clean_toc_title(whole_line.group(1))
            page_token = whole_line.group(2)
            page_number = int(page_token) if page_token.isdigit() else parse_roman_numeral(page_token)
            if title and page_number is not None and is_meaningful_title(title):
                return [(title, page_number)]
        
    # 章节起始锚点扫描正则
    anchor_regex = re.compile(
        r'(?:^|\s+)'
        r'('
        r'第[零一二三四五六七八九十百\d]+[章节]|'
        r'\d+\.\d+(?:\.\d+)?|'
        r'\d{1,2}\s*[A-Za-z\u4e00-\u9fa5]|'
        r'前[\s\-_—–一]*言?|目\s*次|目\s*录|附\s*录[A-Za-z0-9\u4e00-\u9fa5]*|'
        r'参考文献|编制说明|致谢|引言|序言'
        r')'
    )
    
    matches = list(anchor_regex.finditer(row_text))
    results = []
    
    if matches:
        for i in range(len(matches)):
            start = matches[i].start(1)
            end = matches[i+1].start(1) if i + 1 < len(matches) else len(row_text)
            chunk = row_text[start:end].strip()
            
            # 提取末尾的阿拉伯数字或罗马数字页码
            m_page = re.search(r'^(.*?)(?:[.·…\-_﹍\s]{1,}|(?<=[\u4e00-\u9fa5A-Za-z]))(\d{1,4}|[IVXLCDMivxlcdm]+)\s*$', chunk)
            if m_page:
                raw_t = m_page.group(1).strip()
                raw_p = m_page.group(2).strip()
                
                # 转换页码
                if raw_p.isdigit():
                    p_num = int(raw_p)
                else:
                    p_num = parse_roman_numeral(raw_p)
                    
                cleaned_t = clean_toc_title(raw_t)
                if cleaned_t and p_num is not None and is_meaningful_title(cleaned_t):
                    results.append((cleaned_t, p_num))
    else:
        # 备用常规提取
        m_page = re.search(r'^(.*?)(?:[.·…\-_﹍\s]{1,}|(?<=[\u4e00-\u9fa5A-Za-z]))(\d{1,4})\s*$', row_text)
        if m_page:
            cleaned_t = clean_toc_title(m_page.group(1))
            p_num = int(m_page.group(2))
            if cleaned_t and is_meaningful_title(cleaned_t):
                results.append((cleaned_t, p_num))
                
    return results

def parse_toc_from_pages(doc: fitz.Document, toc_pages: List[int]) -> List[TOCItem]:
    """
    从给定的目录物理页列表中高鲁棒性解析书签结构。
    """
    total_pdf_pages = len(doc)
    all_extracted_items = []
    
    for pno in toc_pages:
        if pno < 1 or pno > len(doc):
            continue
        page = doc[pno - 1]
        page_dict = page.get_text("dict")
        page_height = page.rect.height
        
        # 收集所有文本 span
        spans_list = []
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    span_text = span.get("text", "").strip()
                    if not span_text:
                        continue
                    bbox = span.get("bbox")
                    x0, y0, x1, y1 = bbox
                    
                    if y0 < page_height * 0.08 and is_ignorable_header_or_noise(span_text, total_pdf_pages):
                        continue
                    if y1 > page_height * 0.94 and (len(span_text) <= 6 or re.match(r'^\d+$', span_text)):
                        continue
                        
                    spans_list.append({
                        "text": span_text,
                        "x0": x0,
                        "y0": (y0 + y1) / 2.0,
                        "height": y1 - y0
                    })
                    
        if not spans_list:
            continue
            
        # 按 Y 坐标聚类同一物理行
        spans_list.sort(key=lambda s: (s["y0"], s["x0"]))
        
        rows = []
        for span in spans_list:
            matched = False
            for r in rows:
                if abs(r["y0"] - span["y0"]) <= 6.0:
                    r["spans"].append(span)
                    r["y0"] = sum(s["y0"] for s in r["spans"]) / len(r["spans"])
                    matched = True
                    break
            if not matched:
                rows.append({"y0": span["y0"], "spans": [span]})
                
        rows.sort(key=lambda r: r["y0"])
        
        for r in rows:
            sorted_spans = sorted(r["spans"], key=lambda s: s["x0"])
            row_text = " ".join([s["text"] for s in sorted_spans]).strip()
            
            if not row_text or is_ignorable_header_or_noise(row_text, total_pdf_pages):
                continue
                
            items = extract_items_from_row_text(row_text)
            for title, p_num in items:
                all_extracted_items.append((title, p_num, pno))
                
    if not all_extracted_items:
        return []

    # 计算层级与构建 TOCItem
    toc_results = []
    for title, p_num, pno in all_extracted_items:
        lvl = guess_level_by_numbering(title)
        if lvl is None:
            lvl = 1
            
        toc_results.append(TOCItem(
            title=title,
            logical_page=p_num,
            level=lvl,
            raw_text=title,
            source_pdf_page=pno
        ))
        
    # 规范化层级关系
    if toc_results:
        toc_results[0].level = 1
        for i in range(1, len(toc_results)):
            prev_l = toc_results[i - 1].level
            curr_l = toc_results[i].level
            if curr_l > prev_l + 1:
                toc_results[i].level = prev_l + 1
            elif curr_l < 1:
                toc_results[i].level = 1
                
    return toc_results


def _parse_margin_page_token(text: str) -> Optional[int]:
    """解析目录右侧独立页码，容忍点引线残留。"""
    token = re.sub(r"^[.·…_\-\s]+|[.·…_\-\s]+$", "", text)
    token = re.sub(r"^[（(]\s*|\s*[）)]$", "", token)
    if re.fullmatch(r"\d{1,4}", token):
        return int(token)
    if re.fullmatch(r"[IVXLCDMivxlcdm]{1,8}", token):
        return parse_roman_numeral(token)
    return None


def _fit_page_number_to_document(value: int, max_page_number: Optional[int]) -> Optional[int]:
    """修正点引线造成的末位重复；无法落入文档页数范围时返回 None。"""
    if max_page_number is None or value <= max_page_number:
        return value
    digits = str(value)
    while value > max_page_number and len(digits) >= 2 and digits[-1] == digits[-2]:
        digits = digits[:-1]
        value = int(digits)
    return value if value <= max_page_number else None


def _ocr_right_margin_page_numbers(
    page: fitz.Page,
    dpi: int = 250,
    max_page_number: Optional[int] = None,
) -> List[Tuple[float, int]]:
    """单独识别目录右侧页码列，避免点引线令小数字漏检。"""
    from .image_preprocess import preprocess_page_image
    from .ocr_engine import get_ocr_engine

    pix = page.get_pixmap(dpi=dpi)
    image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    array = np.asarray(image)
    height, width = array.shape[:2]

    # 不再假设页码固定在 82%～95%。对右侧多个候选带分别识别，
    # 窄带负责避开点引线，宽带兼容页码列更靠左的版式。
    scale_y = page.rect.height / height
    candidates = []
    crop_ranges = (
        (0.60, 0.98),
        (0.68, 0.98),
        (0.74, 0.98),
        (0.80, 0.98),
        (0.82, 0.95),
    )
    for crop_index, (left_ratio, right_ratio) in enumerate(crop_ranges):
        left = int(width * left_ratio)
        right = int(width * right_ratio)
        crop = Image.fromarray(array[:, left:right])
        cv_image = preprocess_page_image(crop)
        results, _ = get_ocr_engine()(cv_image, return_word_box=True)
        for result in results or []:
            raw_token = str(result[1])
            value = _parse_margin_page_token(raw_token)
            if value is None:
                continue
            # 点引线贴近页码时，OCR 偶尔会把末位重复一次，如 16 -> 166。
            # 仅在结果超过 PDF 总页数时纠正，避免改动合法的三位页码。
            value = _fit_page_number_to_document(value, max_page_number)
            if value is None:
                continue
            box = result[0]
            y_center = sum(point[1] for point in box) / len(box) * scale_y
            candidates.append((y_center, value, float(result[2]), crop_index))

    # 同一行会被多个裁剪带重复识别。按 Y 聚类后采用多数结果，
    # 避免单个裁剪带产生的高置信度错误覆盖其余正确结果。
    clusters: List[List[Tuple[float, int, float, int]]] = []
    for candidate in sorted(candidates, key=lambda row: row[0]):
        for cluster in clusters:
            cluster_y = sum(row[0] for row in cluster) / len(cluster)
            if abs(cluster_y - candidate[0]) <= 3.0:
                cluster.append(candidate)
                break
        else:
            clusters.append([candidate])

    merged = []
    for cluster in clusters:
        votes = {}
        for y_center, value, confidence, crop_index in cluster:
            vote = votes.setdefault(value, {"crops": set(), "confidence": 0.0, "ys": []})
            vote["crops"].add(crop_index)
            vote["confidence"] += confidence
            vote["ys"].append(y_center)
        best_value, best_vote = max(
            votes.items(),
            key=lambda item: (len(item[1]["crops"]), item[1]["confidence"]),
        )
        y_center = sum(best_vote["ys"]) / len(best_vote["ys"])
        merged.append((y_center, best_value))
    return sorted(merged, key=lambda row: row[0])


def _ocr_left_numbering_tokens(page: fitz.Page, dpi: int = 250) -> List[Tuple[float, str]]:
    """单独识别目录左侧编号列，避免点引线把 5、6、7 识别成字母。"""
    from .image_preprocess import preprocess_page_image
    from .ocr_engine import get_ocr_engine

    pix = page.get_pixmap(dpi=dpi)
    image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    array = np.asarray(image)
    height, width = array.shape[:2]
    crop = Image.fromarray(array[:, int(width * 0.05):int(width * 0.18)])
    results, _ = get_ocr_engine()(preprocess_page_image(crop), return_word_box=True)
    scale_y = page.rect.height / height
    tokens = []
    for result in results or []:
        token = re.sub(r"\s+", "", str(result[1])).replace("。", ".")
        if not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){0,3}", token):
            continue
        y_center = sum(point[1] for point in result[0]) / len(result[0]) * scale_y
        x0 = min(point[0] for point in result[0])
        tokens.append((y_center, x0, token))
    # 裁剪 OCR 也可能把两位章节号拆成“1”和“0”，按同一行重建。
    rows = []
    for y_center, x0, token in sorted(tokens):
        for row in rows:
            if abs(row[0] - y_center) <= 3.0:
                row[1].append((x0, token))
                break
        else:
            rows.append((y_center, [(x0, token)]))
    return [(y, ''.join(token for _, token in sorted(parts))) for y, parts in rows]


def _rebuild_ocr_title_rows(
    page: fitz.Page,
    ocr_items: List[dict],
) -> List[Tuple[float, str]]:
    """把同一目录行中被 OCR 拆开的编号和标题按几何位置重新拼接。"""
    fragments = []
    for item in ocr_items:
        box = item.get("box") or []
        text = str(item.get("text", "")).strip()
        if not box or not text:
            continue
        x0 = min(point[0] for point in box)
        if x0 > page.rect.width * 0.75:
            continue
        y_center = sum(point[1] for point in box) / len(box)
        fragments.append((y_center, x0, text))

    rows: List[dict] = []
    for y_center, x0, text in sorted(fragments):
        for row in rows:
            if abs(row["y"] - y_center) <= 3.0:
                row["parts"].append((x0, text))
                row["ys"].append((y_center, text))
                row["y"] = sum(part[0] for part in row["ys"]) / len(row["ys"])
                break
        else:
            rows.append({"y": y_center, "parts": [(x0, text)], "ys": [(y_center, text)]})

    rebuilt = []
    for row in rows:
        text = " ".join(part[1] for part in sorted(row["parts"])).strip()
        text = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", text)
        rebuilt.append((row["y"], text))
    return rebuilt


def parse_toc_from_ocr_pages(
    doc: fitz.Document,
    toc_pages: List[int],
    dpi: int = 200,
) -> List[TOCItem]:
    """
    直接从目录图片的 OCR 几何结果解析目录。

    标题区和右侧页码列分别识别，再按 Y 坐标配对。这样不会依赖生成后的
    PDF 文本流，也不会因字符级隐藏文字层将 10 / 11 拆开而丢失首位数字。
    """
    from .ocr_engine import ocr_pdf_page

    output: List[TOCItem] = []
    for physical_page in toc_pages:
        if physical_page < 1 or physical_page > len(doc):
            continue
        page = doc[physical_page - 1]
        ocr_items, _ = ocr_pdf_page(page, dpi=dpi)
        margin_numbers = _ocr_right_margin_page_numbers(
            page, max_page_number=len(doc)
        )
        left_numbers = _ocr_left_numbering_tokens(page)
        unused_numbers = set(range(len(margin_numbers)))

        title_rows: List[Tuple[float, str]] = []
        for y_center, raw_text in _rebuild_ocr_title_rows(page, ocr_items):
            if is_ignorable_header_or_noise(raw_text, len(doc)):
                continue

            # 使用窄裁剪得到的左侧编号修复被点引线干扰的 5 / 6 / 7 等编号。
            nearby_left = [
                (abs(y_center - token_y), token)
                for token_y, token in left_numbers
                if abs(y_center - token_y) <= 4.0
            ]
            if nearby_left:
                left_token = min(nearby_left)[1]
                numbering = re.match(r"^\d{1,3}(?:\s*\.\s*\d{1,3})*", raw_text)
                # 整页识别漏掉多位编号首位时，窄列完整编号可用于补回。
                missing_prefix = (numbering and left_token != numbering.group()
                                  and left_token.endswith(numbering.group())
                                  and len(left_token) > len(numbering.group()))
                if not numbering or missing_prefix:
                    chinese = re.search(r"[\u4e00-\u9fa5]", raw_text)
                    if chinese:
                        raw_text = f"{left_token} {raw_text[chinese.start():]}"

            title = clean_toc_title(raw_text)
            compact = re.sub(r"\s+", "", title)
            if compact in {"目录", "目次"}:
                pass
            elif not re.match(
                r"^(?:前言|序言|引言|附录|参考文献|本规范用词说明|引用标准名录|附[：:]?条文说明|"
                r"\d{1,3}(?:\.\d{1,3}){0,3}\s*[A-Za-z\u4e00-\u9fa5])",
                title,
            ):
                continue

            title_rows.append((y_center, title))

        for title_y, title in title_rows:
            candidates = [
                (abs(title_y - margin_numbers[index][0]), index)
                for index in unused_numbers
                if abs(title_y - margin_numbers[index][0]) <= 8.0
            ]
            if not candidates:
                continue
            _, best_index = min(candidates)
            unused_numbers.remove(best_index)
            logical_page = margin_numbers[best_index][1]
            level = guess_level_by_numbering(title) or 1
            output.append(TOCItem(
                title=title,
                logical_page=logical_page,
                level=level,
                raw_text=title,
                source_pdf_page=physical_page,
            ))

    if output:
        output[0].level = 1
    return output
