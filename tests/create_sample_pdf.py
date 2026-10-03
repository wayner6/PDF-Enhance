import sys
import os
import pymupdf as fitz

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def generate_sample_pdf(output_path: str = "sample_book.pdf"):
    """
    生成一个具有真实排版特征的模拟电子书 PDF 用于测试：
    - 物理第 1 页: 封面
    - 物理第 2 页: 扉页/版权
    - 物理第 3 页: 前言
    - 物理第 4 页: 目录 (Table of Contents)
    - 物理第 5 页 (逻辑 1): 第一章 计算机基础
    - 物理第 6 页 (逻辑 2): 1.1 硬件体系架构
    - 物理第 7 页 (逻辑 3): 1.2 操作系统概述
    - 物理第 8 页 (逻辑 4): 第二章 网络与分布式
    - 物理第 9 页 (逻辑 5): 2.1 TCP/IP 协议栈
    """
    doc = fitz.open()

    # 寻找系统可用中文字体
    chinese_font = "C:/Windows/Fonts/simhei.ttf"
    if not os.path.exists(chinese_font):
        chinese_font = "C:/Windows/Fonts/msyh.ttc"
    
    def add_page_text(page, x, y, text, size=12):
        if os.path.exists(chinese_font):
            page.insert_font(fontname="f0", fontfile=chinese_font, set_simple=False)
            page.insert_text((x, y), text, fontname="f0", fontsize=size)
        else:
            page.insert_text((x, y), text, fontsize=size)

    # 1. 封面
    p1 = doc.new_page()
    add_page_text(p1, 72, 150, "计算机科学导论", size=30)
    add_page_text(p1, 72, 200, "示例测试教材", size=16)

    # 2. 扉页
    p2 = doc.new_page()
    add_page_text(p2, 72, 200, "版权所有 © 2025", size=12)

    # 3. 前言
    p3 = doc.new_page()
    add_page_text(p3, 72, 100, "前言", size=20)
    add_page_text(p3, 72, 150, "本书专为测试 PDF 书签生成工具而设计。", size=12)

    # 4. 目录页
    p4 = doc.new_page()
    add_page_text(p4, 250, 80, "目  录", size=22)
    
    # 模拟目录内容（含点号引线、不同缩进）
    toc_lines = [
        ("第一章 计算机基础 .......................................... 1", 72, 130),
        ("    1.1 硬件体系架构 ...................................... 2", 72, 160),
        ("    1.2 操作系统概述 ...................................... 3", 72, 190),
        ("第二章 网络与分布式 ...................................... 4", 72, 230),
        ("    2.1 TCP/IP 协议栈 ..................................... 5", 72, 260),
    ]
    for text, x, y in toc_lines:
        add_page_text(p4, x, y, text, size=12)

    # 5. 正文第 1 页 (物理 5)
    p5 = doc.new_page()
    add_page_text(p5, 72, 100, "第一章 计算机基础", size=20)
    add_page_text(p5, 72, 150, "计算机是现代信息社会的基石...", size=12)
    add_page_text(p5, 280, 780, "- 1 -", size=10)

    # 6. 正文第 2 页 (物理 6)
    p6 = doc.new_page()
    add_page_text(p6, 72, 100, "1.1 硬件体系架构", size=18)
    add_page_text(p6, 72, 150, "冯诺依曼结构包含五大核心组件...", size=12)
    add_page_text(p6, 280, 780, "- 2 -", size=10)

    # 7. 正文第 3 页 (物理 7)
    p7 = doc.new_page()
    add_page_text(p7, 72, 100, "1.2 操作系统概述", size=18)
    add_page_text(p7, 72, 150, "操作系统是管理软硬件资源的核心系统软件...", size=12)
    add_page_text(p7, 280, 780, "- 3 -", size=10)

    # 8. 正文第 4 页 (物理 8)
    p8 = doc.new_page()
    add_page_text(p8, 72, 100, "第二章 网络与分布式", size=20)
    add_page_text(p8, 72, 150, "网络将孤立的计算节点互联...", size=12)
    add_page_text(p8, 280, 780, "- 4 -", size=10)

    # 9. 正文第 5 页 (物理 9)
    p9 = doc.new_page()
    add_page_text(p9, 72, 100, "2.1 TCP/IP 协议栈", size=18)
    add_page_text(p9, 72, 150, "分层模型与数据包封装机制...", size=12)
    add_page_text(p9, 280, 780, "- 5 -", size=10)

    doc.save(output_path)
    doc.close()
    print(f"[OK] 成功创建测试 PDF: {output_path}")

if __name__ == "__main__":
    generate_sample_pdf()
