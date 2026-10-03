import json
import base64
from typing import List, Optional, Dict, Any
import pymupdf as fitz
from openai import OpenAI

from .parser import TOCItem
from .config import load_ai_config

DEFAULT_AI_PROMPT = """你是一个高精度的图书目录结构化解析专家。
请仔细查看我提供的这几张 PDF 目录页图片，提取出完整的章节目录大纲。

输出要求：
1. 必须输出严格的 JSON 数组格式，不要包含 markdown 格式外多余文字。
2. 数组中每个元素包含以下 3 个字段：
   - "title": 章节标题文字（去除点引线和多余乱码符号，保留标准章节编号如 "第一章 概述" 或 "1.1 背景"）
   - "page": 该章节对应的正文逻辑页码（正整数，若为罗马数字前言/目次请转为阿拉伯数字）
   - "level": 标题层级（一级标题为 1，二级子标题为 2，三级为 3）

示例输出格式：
[
  {"title": "前言", "page": 1, "level": 1},
  {"title": "1 总则", "page": 1, "level": 1},
  {"title": "2 基本规定", "page": 3, "level": 1},
  {"title": "2.1 术语", "page": 4, "level": 2}
]
"""

def render_pages_to_base64(doc: fitz.Document, toc_pages: List[int], dpi: int = 150) -> List[str]:
    """将目录页渲染为 base64 图片"""
    images_b64 = []
    for pno in toc_pages:
        if pno < 1 or pno > len(doc):
            continue
        page = doc[pno - 1]
        pix = page.get_pixmap(dpi=dpi)
        img_bytes = pix.tobytes("jpeg")
        b64_str = base64.b64encode(img_bytes).decode("utf-8")
        images_b64.append(b64_str)
    return images_b64

def parse_toc_with_vision_ai(
    doc: fitz.Document, 
    toc_pages: List[int], 
    config: Optional[Dict[str, Any]] = None
) -> Optional[List[TOCItem]]:
    """
    使用多模态视觉大模型（Vision LLM）解析目录页。
    """
    cfg = config or load_ai_config()
    api_key = cfg.get("api_key", "").strip()
    if not api_key:
        return None
        
    base_url = cfg.get("base_url", "https://api.openai.com/v1").strip()
    model_name = cfg.get("model", "gpt-4o-mini").strip()
    
    images_b64 = render_pages_to_base64(doc, toc_pages)
    if not images_b64:
        return None
        
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        
        # 构造多模态消息内容
        content = [{"type": "text", "text": DEFAULT_AI_PROMPT}]
        for img_b64 in images_b64:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{img_b64}",
                    "detail": "high"
                }
            })
            
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "user", "content": content}
            ],
            temperature=0.1
        )
        
        reply_text = response.choices[0].message.content.strip()
        
        # 提取 JSON 块
        if "```json" in reply_text:
            reply_text = reply_text.split("```json")[1].split("```")[0].strip()
        elif "```" in reply_text:
            reply_text = reply_text.split("```")[1].split("```")[0].strip()
            
        data = json.loads(reply_text)
        if not isinstance(data, list):
            return None
            
        toc_items = []
        for idx, entry in enumerate(data):
            title = str(entry.get("title", "")).strip()
            page = int(entry.get("page", 1))
            level = int(entry.get("level", 1))
            if title:
                toc_items.append(TOCItem(
                    title=title,
                    logical_page=page,
                    level=max(1, min(level, 4)),
                    raw_text=title,
                    source_pdf_page=toc_pages[0] if toc_pages else 1
                ))
        return toc_items if toc_items else None
    except Exception as e:
        print(f"Vision AI 调用异常: {str(e)}")
        return None
