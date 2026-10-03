import re
from typing import List
from .parser import TOCItem

HEADER_COMMENT = """# ==========================================
# PDF-Enhance 目录书签校对文件
# 
# 规则说明：
# 1. 使用 Tab 键或空格缩进表示层级 (无缩进=一级标题, 1个Tab/4空格=二级, 依此类推)
# 2. 标题与页码之间使用 Tab 或空格隔开
# 3. 此处的页码为逻辑页码（在写回 PDF 时会自动加上您设置的偏移量）
# 4. 以 '#' 开头的行会被忽略
# ==========================================
"""

def export_to_toc_file(toc_items: List[TOCItem], filepath: str) -> None:
    """
    将解析出的 TOCItem 列表导出为易于人工阅读和修改的文本文件。
    """
    lines = [HEADER_COMMENT]
    for item in toc_items:
        indent = "\t" * (item.level - 1)
        # 用 Tab 分隔标题和页码，方便在各类编辑器中查看
        lines.append(f"{indent}{item.title}\t{item.logical_page}")
        
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def import_from_toc_file(filepath: str) -> List[TOCItem]:
    """
    从文本文件中读取人工修改后的目录书签。
    """
    items = []
    invalid_lines = []
    with open(filepath, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()
        
    for line_idx, line in enumerate(raw_lines, start=1):
        line = line.rstrip("\r\n")
        # 忽略空行和注释行
        if not line.strip() or line.strip().startswith("#"):
            continue
            
        # 计算缩进层级
        indent_level = 1
        stripped = line.lstrip("\t")
        tab_count = len(line) - len(stripped)
        
        if tab_count > 0:
            indent_level = tab_count + 1
        else:
            # 检测空格缩进（每 2~4 个空格算一级）
            space_stripped = line.lstrip(" ")
            spaces = len(line) - len(space_stripped)
            if spaces > 0:
                indent_level = (spaces // 2) + 1
            stripped = space_stripped
            
        # 解析标题与页码
        # 匹配末尾的数字作为页码
        match = re.search(r'^(.*?)(?:[\t\s]{1,})(\d+)\s*$', stripped)
        if match:
            title = match.group(1).strip()
            page_num = int(match.group(2).strip())
            items.append(TOCItem(
                title=title,
                logical_page=page_num,
                level=min(indent_level, 4)
            ))
        else:
            invalid_lines.append(line_idx)

    if invalid_lines:
        display = ", ".join(str(number) for number in invalid_lines[:10])
        suffix = "…" if len(invalid_lines) > 10 else ""
        raise ValueError(f"以下行缺少有效页码：{display}{suffix}。请在每行末尾填写页码。")

    return items
