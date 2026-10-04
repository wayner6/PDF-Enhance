import os
import sys
import ctypes
from typing import List, Dict, Any, Tuple, Optional
import numpy as np
from PIL import Image
import pymupdf as fitz
from rapidocr_onnxruntime import RapidOCR

from .image_preprocess import preprocess_page_image

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

_OCR_INSTANCE: Optional[RapidOCR] = None

def set_process_low_priority():
    """设置进程低优先级"""
    if sys.platform == "win32":
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.SetPriorityClass.restype = ctypes.c_bool
            h_proc = kernel32.GetCurrentProcess()
            kernel32.SetPriorityClass(h_proc, 0x00004000)
        except Exception:
            pass

def get_ocr_engine() -> RapidOCR:
    global _OCR_INSTANCE
    if _OCR_INSTANCE is None:
        _OCR_INSTANCE = RapidOCR(
            det_limit_side_len=1200,
            det_box_thresh=0.3,
            det_unclip_ratio=1.8,
            # 并发由外层进程池控制，避免每个 ONNX 会话再启动多线程争抢 CPU。
            intra_op_num_threads=1,
            inter_op_num_threads=1,
        )
    return _OCR_INSTANCE

def ocr_cv_image(cv_img: np.ndarray) -> List[Dict[str, Any]]:
    """执行 OCR，并保留识别器计算出的字符/单词级坐标框。"""
    engine = get_ocr_engine()
    results, _ = engine(cv_img, return_word_box=True)
    if not results:
        return []

    formatted = []
    for item in results:
        # return_word_box=True 时：
        # [line_box, text, score, word_boxes, word_contents, word_scores]
        box = item[0]
        text = item[1]
        score = item[2]
        word_boxes = item[3] if len(item) > 3 else []
        word_contents = item[4] if len(item) > 4 else []
        word_scores = item[5] if len(item) > 5 else []
        formatted.append({
            "text": text.strip(),
            "box": box,
            "score": float(score),
            "word_boxes": word_boxes,
            "word_contents": word_contents,
            "word_scores": word_scores,
        })
    return formatted

def ocr_pdf_page(doc_or_page: fitz.Page, dpi: int = 150) -> Tuple[List[Dict[str, Any]], Tuple[float, float]]:
    pix = doc_or_page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
    # 内存中的像素直接交给预处理，省去每页 PNG 压缩和解码。
    pil_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    
    cv_img = preprocess_page_image(pil_img)
    ocr_results = ocr_cv_image(cv_img)
    
    img_w, img_h = pix.width, pix.height
    pdf_w = doc_or_page.rect.width
    pdf_h = doc_or_page.rect.height
    
    scale_x = pdf_w / img_w
    scale_y = pdf_h / img_h
    
    scaled_results = []
    for item in ocr_results:
        scaled_box = []
        for pt in item["box"]:
            scaled_box.append([pt[0] * scale_x, pt[1] * scale_y])
        scaled_word_boxes = []
        for word_box in item.get("word_boxes", []):
            scaled_word_boxes.append([
                [pt[0] * scale_x, pt[1] * scale_y]
                for pt in word_box
            ])

        scaled_results.append({
            "text": item["text"],
            "box": scaled_box,
            "score": item["score"],
            "word_boxes": scaled_word_boxes,
            "word_contents": item.get("word_contents", []),
            "word_scores": item.get("word_scores", []),
        })
        
    return scaled_results, (pdf_w, pdf_h)

def cached_page_ocr(page: fitz.Page, dpi: int, cache: Optional[dict] = None):
    """单个任务内复用页面 OCR；键为零基页码，不能跨不同 PDF 共用。

    坐标已经换算成 PDF 点，高分辨率结果可供目录探测等低 DPI 阶段复用。
    """
    if cache is None:
        return ocr_pdf_page(page, dpi=dpi)
    if page.number not in cache:
        cache[page.number], _ = ocr_pdf_page(page, dpi=dpi)
    return cache[page.number], (page.rect.width, page.rect.height)


def worker_ocr_init():
    set_process_low_priority()

def worker_ocr_page_task(
    args: Tuple[str, int, int]
) -> Tuple[int, List[Dict[str, Any]], Tuple[float, float], Optional[str]]:
    """子进程 OCR；异常会显式返回主进程，避免把失败页误报为成功。"""
    pdf_path, pno_0based, dpi = args
    try:
        with fitz.open(pdf_path) as doc:
            page = doc[pno_0based]
            results, (pw, ph) = ocr_pdf_page(page, dpi=dpi)
        return pno_0based, results, (pw, ph), None
    except Exception as exc:
        return pno_0based, [], (0.0, 0.0), f"{type(exc).__name__}: {exc}"
