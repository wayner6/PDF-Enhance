import os
import re
import tempfile
from typing import List, Tuple, Optional
import pymupdf as fitz
from .cancellation import check_cancelled
from .parser import TOCItem
from .detector import usable_page_text

def find_title_coordinate_on_page(page: fitz.Page, title: str) -> Optional[fitz.Point]:
    """
    在指定的物理页面中寻找标题所在的精确 (x, y) 坐标。
    返回精确跳转点 Point(x, y)，若未找到则返回 None。
    """
    check_cancelled()
    # PDF 字符级文字层也会把编号和标题拆成多个 span。和 OCR 共用
    # 几何行匹配，不去掉章节编号后搜索正文词语，也不使用标题前四字兜底。
    items = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                x0, y0, x1, y1 = span["bbox"]
                items.append({"text": span["text"], "box": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]})
    return find_title_coordinate_from_ocr_items(items, title)

def find_title_coordinate_from_ocr_items(
    ocr_items: list,
    title: str,
) -> Optional[fitz.Point]:
    """优先按“章节编号 + 完整标题”查找坐标，避免命中正文中的同名词语。"""
    check_cancelled()
    # 保留编号内的点，避免 3.3、33、3.3.1 被当成同一个编号。
    normalize = lambda value: re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5.]", "", value).lower()
    target = normalize(title)
    if not target:
        return None

    numbered = bool(re.match(r"^(?:第[零一二三四五六七八九十百\d]+[章节]|\d+(?:\.\d+)*)", target))
    fragments = []
    for item in ocr_items:
        box = item.get("box") or []
        text = str(item.get("text", "")).strip()
        if not box or not text:
            continue
        fragments.append({
            "text": text,
            "x0": min(point[0] for point in box),
            "y0": min(point[1] for point in box),
            "yc": sum(point[1] for point in box) / len(box),
        })

    # OCR 可能把“7.2”和“技术状况评价”拆成两个框，先按 Y 坐标重建文本行。
    rows = []
    for fragment in sorted(fragments, key=lambda row: (row["yc"], row["x0"])):
        check_cancelled()
        for row in rows:
            if abs(row["yc"] - fragment["yc"]) <= 3.0:
                row["parts"].append(fragment)
                row["yc"] = sum(part["yc"] for part in row["parts"]) / len(row["parts"])
                break
        else:
            rows.append({"yc": fragment["yc"], "parts": [fragment]})

    candidates = []
    for row in rows:
        parts = sorted(row["parts"], key=lambda part: part["x0"])
        row_text = normalize(" ".join(part["text"] for part in parts))
        if (numbered and not row_text.startswith(target)) or target not in row_text:
            continue
        # 完全相等优先，其次是以完整标题开头，最后才是包含关系。
        rank = 2 if row_text == target else (1 if row_text.startswith(target) else 0)
        candidates.append((rank, -abs(len(row_text) - len(target)), -row["yc"], parts))

    if candidates:
        parts = max(candidates, key=lambda candidate: candidate[:3])[3]
        x0 = min(part["x0"] for part in parts)
        y0 = min(part["y0"] for part in parts)
        return fitz.Point(x0, max(0.0, y0 - 15.0))

    # 找不到完整标题时宁可跳到页首，不跳到正文中的同名词语。
    return None


def apply_bookmarks(
    input_pdf_path: str,
    output_pdf_path: str,
    toc_items: List[TOCItem],
    page_offset: int = 0,
    use_ocr_coordinates: bool = False,
    ocr_dpi: int = 150,
    ocr_cache: Optional[dict] = None,
    ocr_fallback: bool = False,
) -> Tuple[bool, int, str]:
    """
    将书签写入 PDF 并另存为新文件。
    支持高精度坐标定位跳转：同一页内不同位置的章节可精准定位到对应位置。
    
    返回 (成功状态, 写入条目数, 提示信息)
    """
    check_cancelled()
    if not os.path.exists(input_pdf_path):
        return False, 0, f"输入文件不存在: {input_pdf_path}"
        
    if not toc_items:
        return False, 0, "书签列表为空，未写入任何书签"
    if os.path.abspath(input_pdf_path) == os.path.abspath(output_pdf_path):
        return False, 0, "输出文件不能与输入文件相同"

    output_dir = os.path.dirname(os.path.abspath(output_pdf_path))
    if not os.path.isdir(output_dir):
        return False, 0, f"输出目录不存在: {output_dir}"

    doc = None
    temp_output = None
    try:
        doc = fitz.open(input_pdf_path)
        total_pages = len(doc)
        
        invalid_targets = []
        for item in toc_items:
            if item.title.strip() in {"目次", "目录", "前言", "序言", "编制说明"}:
                continue
            target = item.logical_page + page_offset
            if target < 1 or target > total_pages:
                invalid_targets.append((item.title, target))
        if invalid_targets:
            examples = "，".join(
                f"{title} -> {page}" for title, page in invalid_targets[:5]
            )
            return False, 0, (
                f"有 {len(invalid_targets)} 个书签页码超出 PDF 范围：{examples}。"
                "请检查目录识别结果或页码偏移。"
            )

        fitz_toc = []
        clipped_count = 0
        precise_count = 0
        ocr_cache = ocr_cache if ocr_cache is not None else {}
        
        # 预先进行层级归一化，防止 PyMuPDF 报 hierarchy level 错误
        normalized_levels = []
        for i, item in enumerate(toc_items):
            raw_lvl = max(1, item.level)
            if i == 0:
                normalized_levels.append(1)
            else:
                prev_lvl = normalized_levels[i - 1]
                if raw_lvl > prev_lvl + 1:
                    normalized_levels.append(prev_lvl + 1)
                else:
                    normalized_levels.append(raw_lvl)
                    
        for i, item in enumerate(toc_items):
            check_cancelled()
            target_pno = item.logical_page + page_offset
            
            # 特殊前置项（目次、前言）智能物理页矫正：
            clean_t = item.title.strip()
            if clean_t in ["目次", "目录"]:
                # 目次直接跳转到其来源的目录物理页
                target_pno = item.source_pdf_page
            elif clean_t in ["前言", "序言", "编制说明"]:
                # 先查目录后的页面，再查目录前的页面，并兼容“前\n言”这类拆字文本。
                nearby_pages = [
                    item.source_pdf_page + distance for distance in range(1, 4)
                ] + [
                    item.source_pdf_page - distance for distance in range(1, 4)
                ]
                compact_title = re.sub(r"\s+", "", clean_t)
                for check_p in nearby_pages:
                    if check_p < 1 or check_p > total_pages:
                        continue
                    page_obj = doc[check_p - 1]
                    text = usable_page_text(page_obj)
                    if not text and (ocr_fallback or use_ocr_coordinates):
                        from .ocr_engine import cached_page_ocr
                        rows, _ = cached_page_ocr(page_obj, ocr_dpi, ocr_cache)
                        text = "\n".join(str(row.get("text", "")) for row in rows)
                    page_text = re.sub(r"\s+", "", text)
                    position = page_text.find(compact_title)
                    if position >= 0 and position <= max(100, len(page_text) // 5):
                        target_pno = check_p
                        break
            
            if target_pno < 1:
                target_pno = 1
                clipped_count += 1
            elif target_pno > total_pages:
                target_pno = total_pages
                clipped_count += 1
                
            lvl = normalized_levels[i]
            page_obj = doc[target_pno - 1]
            
            # 扫描件或损坏文字层优先使用 OCR 坐标，避免 PDF 内部乱码匹配到正文。
            coord_point = None
            if use_ocr_coordinates or target_pno - 1 in ocr_cache:
                from .ocr_engine import cached_page_ocr
                items, _ = cached_page_ocr(page_obj, ocr_dpi, ocr_cache)
                coord_point = find_title_coordinate_from_ocr_items(items, item.title)
            if coord_point is None and usable_page_text(page_obj):
                coord_point = find_title_coordinate_on_page(page_obj, item.title)
            if coord_point is None and ocr_fallback:
                from .ocr_engine import cached_page_ocr
                items, _ = cached_page_ocr(page_obj, ocr_dpi, ocr_cache)
                coord_point = find_title_coordinate_from_ocr_items(items, item.title)
            
            if coord_point is not None:
                precise_count += 1
                # 使用 PyMuPDF 的精确目标字典设置跳转目的地
                dest = {
                    "kind": fitz.LINK_GOTO,
                    "page": target_pno - 1,
                    "to": coord_point,
                    "zoom": 0.0  # 保持当前缩放比例不变
                }
                fitz_toc.append([lvl, item.title, target_pno, dest])
            else:
                # 兜底默认跳到页首
                fitz_toc.append([lvl, item.title, target_pno])
            
        # 设置大纲
        doc.set_toc(fitz_toc)
        
        # 先写入同目录临时文件，成功后原子替换目标，避免中断时留下损坏文件。
        temp_fd, temp_output = tempfile.mkstemp(
            prefix=".pdf-enhance-bookmark-", suffix=".pdf", dir=output_dir
        )
        os.close(temp_fd)
        os.unlink(temp_output)
        check_cancelled()
        doc.save(temp_output, garbage=3, deflate=True)
        doc.close()
        doc = None
        check_cancelled()
        os.replace(temp_output, output_pdf_path)
        temp_output = None
        
        msg = f"已写入 {len(fitz_toc)} 个书签，其中 {precise_count} 个定位到标题位置。"
        if precise_count < len(fitz_toc):
            msg += f" {len(fitz_toc) - precise_count} 个未精确定位的书签跳转到对应页页首。"
        if clipped_count > 0:
            msg += f" {clipped_count} 个页码超出范围，已移到最近的有效页面。"
            
        return True, len(fitz_toc), msg
        
    except Exception as e:
        return False, 0, f"写入书签时发生错误: {str(e)}"
    finally:
        if doc is not None:
            doc.close()
        if temp_output and os.path.exists(temp_output):
            try:
                os.remove(temp_output)
            except OSError:
                pass
