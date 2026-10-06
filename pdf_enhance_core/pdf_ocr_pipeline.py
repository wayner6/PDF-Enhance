import os
import tempfile
from multiprocessing import Pool, TimeoutError, cpu_count
from typing import Any, Callable, Dict, List, Optional, Tuple

import pymupdf as fitz
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .cancellation import check_cancelled
from .ocr_engine import worker_ocr_init, worker_ocr_page_task

console = Console()
_CJK_FONT: Optional[fitz.Font] = None


def get_cjk_font() -> fitz.Font:
    global _CJK_FONT
    if _CJK_FONT is None:
        _CJK_FONT = fitz.Font("china-s")
    return _CJK_FONT


def _rect_from_quad(box: List[List[float]]) -> fitz.Rect:
    xs = [point[0] for point in box]
    ys = [point[1] for point in box]
    return fitz.Rect(min(xs), min(ys), max(xs), max(ys))


def _append_exact_words(writer: fitz.TextWriter, item: Dict[str, Any]) -> bool:
    """按 RapidOCR 的字符/单词框写入，成功时返回 True。"""
    boxes = item.get("word_boxes") or []
    contents = item.get("word_contents") or []
    if not boxes or len(boxes) != len(contents):
        return False

    font = get_cjk_font()
    appended = False
    for content, box in zip(contents, boxes):
        text = str(content)
        if not text or not box:
            continue

        rect = _rect_from_quad(box)
        if rect.is_empty or rect.width < 0.5 or rect.height < 0.5:
            continue

        # 单字框由识别器的 CTC 对齐结果计算，不再用整行长度猜测位置。
        # 字号同时受框高和框宽限制，避免高亮框溢出相邻字符。
        natural_width = max(0.1, get_cjk_font().text_length(text, fontsize=1.0))
        font_size = min(rect.height * 0.86, rect.width / natural_width)
        font_size = max(3.0, min(font_size, 36.0))
        baseline = rect.y1 - rect.height * 0.12
        writer.append(fitz.Point(rect.x0, baseline), text, font=font, fontsize=font_size)
        appended = True

    return appended


def _append_line_fallback(writer: fitz.TextWriter, item: Dict[str, Any]) -> None:
    """旧模型没有字符框时的降级路径；仅保证可搜索，不承诺字符级精度。"""
    text = str(item.get("text", "")).strip()
    box = item.get("box") or []
    if not text or not box:
        return

    rect = _rect_from_quad(box)
    if rect.is_empty:
        return

    font = get_cjk_font()
    natural_width = max(0.1, font.text_length(text, fontsize=1.0))
    font_size = min(rect.height * 0.86, rect.width / natural_width)
    font_size = max(3.0, min(font_size, 36.0))
    baseline = rect.y1 - rect.height * 0.12
    writer.append(fitz.Point(rect.x0, baseline), text, font=font, fontsize=font_size)


def remove_existing_text_from_scan_page(page: fitz.Page) -> bool:
    """移除全页扫描图上的旧文字层，同时保留底图和线条；非扫描页不处理。"""
    page_area = max(page.rect.get_area(), 1.0)
    image_coverage = max(
        (
            fitz.Rect(info["bbox"]).get_area() / page_area
            for info in page.get_image_info()
            if info.get("bbox")
        ),
        default=0.0,
    )
    if image_coverage < 0.8 or not page.get_text().strip():
        return False

    page.add_redact_annot(page.rect, fill=False)
    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_NONE,
        graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        text=fitz.PDF_REDACT_TEXT_REMOVE,
    )
    return True


def inject_invisible_text_layer(page: fitz.Page, ocr_items: List[Dict[str, Any]]) -> None:
    """批量注入字符级精确、不可见且可搜索的文字层。"""
    if not ocr_items:
        return

    writer = fitz.TextWriter(page.rect)
    for item in ocr_items:
        if not _append_exact_words(writer, item):
            _append_line_fallback(writer, item)
    writer.write_text(page, render_mode=3)


def generate_searchable_pdf(
    input_pdf_path: str,
    output_pdf_path: str,
    dpi: int = 200,
    max_workers: Optional[int] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    skip_pages_with_text: bool = True,
    replace_existing_scan_text: bool = False,
    ocr_page_callback: Optional[Callable[[int, List[Dict[str, Any]]], None]] = None,
) -> Tuple[bool, str]:
    """将扫描 PDF 转换为带精确隐藏文字层的可搜索 PDF。"""
    check_cancelled()
    if not os.path.exists(input_pdf_path):
        return False, f"输入文件不存在: {input_pdf_path}"
    if os.path.abspath(input_pdf_path) == os.path.abspath(output_pdf_path):
        return False, "输出文件不能与输入文件相同"

    output_dir = os.path.dirname(os.path.abspath(output_pdf_path))
    if not os.path.isdir(output_dir):
        return False, f"输出目录不存在: {output_dir}"

    with fitz.open(input_pdf_path) as source:
        total_pages = len(source)
        if skip_pages_with_text:
            page_indexes = [
                index for index, page in enumerate(source)
                if len(page.get_text().strip()) < 10
            ]
        else:
            page_indexes = list(range(total_pages))
    if total_pages == 0:
        return False, "PDF 页面总数为 0"

    available_cpus = cpu_count()
    worker_count = max_workers or max(1, available_cpus // 2)
    worker_count = max(1, min(worker_count, available_cpus))
    tasks = [(input_pdf_path, page_index, dpi) for page_index in page_indexes]
    results_by_page: Dict[int, List[Dict[str, Any]]] = {}
    failed_pages: Dict[int, str] = {}

    ocr_page_count = len(tasks)
    console.print(
        f"[dim][OCR] 阶段 1/3：启动 {worker_count} 个后台进程，"
        f"识别 {ocr_page_count}/{total_pages} 页...[/dim]"
    )
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]OCR 识别中[/bold cyan]"),
        BarColumn(bar_width=40),
        TextColumn("[bold green]{task.completed}/{task.total} 页[/bold green]"),
        TextColumn("[yellow]({task.percentage:>3.0f}%)[/yellow]"),
        TimeElapsedColumn(),
    ) as progress:
        progress_task = progress.add_task("OCR", total=ocr_page_count)
        with Pool(processes=min(worker_count, max(1, ocr_page_count)), initializer=worker_ocr_init) as pool:
            pending = pool.imap_unordered(worker_ocr_page_task, tasks)
            while len(results_by_page) < ocr_page_count:
                check_cancelled()
                try:
                    page_index, results, _, error = pending.next(timeout=0.1)
                except TimeoutError:
                    continue
                check_cancelled()
                results_by_page[page_index] = results
                if error:
                    failed_pages[page_index] = error
                elif ocr_page_callback:
                    ocr_page_callback(page_index, results)
                progress.advance(progress_task)
                if progress_callback:
                    progress_callback(len(results_by_page), ocr_page_count)

    if failed_pages:
        failed_display = ", ".join(str(index + 1) for index in sorted(failed_pages)[:20])
        suffix = "…" if len(failed_pages) > 20 else ""
        first_error = failed_pages[min(failed_pages)]
        return False, (
            f"有 {len(failed_pages)} 页 OCR 失败（物理页：{failed_display}{suffix}）。"
            f"首个错误：{first_error}。未生成不完整的输出文件。"
        )

    console.print("[dim][OCR] 阶段 2/3：正在按字符真实坐标注入文字层...[/dim]")
    source_doc = fitz.open(input_pdf_path)
    output_doc = fitz.open()
    temp_fd, temp_output = tempfile.mkstemp(prefix=".pdf-enhance-", suffix=".pdf", dir=output_dir)
    os.close(temp_fd)
    os.unlink(temp_output)
    try:
        check_cancelled()
        output_doc.insert_pdf(source_doc)
        metadata = dict(source_doc.metadata or {})
        previous_producer = str(metadata.get("producer") or "").strip()
        metadata["producer"] = (
            f"{previous_producer}; PDF-Enhance {('full-ocr' if not skip_pages_with_text else 'ocr-fill')}"
            if previous_producer
            else f"PDF-Enhance {('full-ocr' if not skip_pages_with_text else 'ocr-fill')}"
        )
        keywords = str(metadata.get("keywords") or "").strip()
        metadata["keywords"] = ", ".join(
            value for value in (keywords, "PDF-Enhance searchable OCR") if value
        )
        output_doc.set_metadata(metadata)
        replaced_text_pages = 0
        for page_index in range(total_pages):
            check_cancelled()
            page = output_doc[page_index]
            if replace_existing_scan_text and remove_existing_text_from_scan_page(page):
                replaced_text_pages += 1
            # 关键：原扫描 PDF 的内容流可能泄漏缩放/平移矩阵。
            # wrap_contents() 将原内容包进 q/Q，确保新文字使用页面标准坐标系。
            page.wrap_contents()
            inject_invisible_text_layer(page, results_by_page.get(page_index, []))

        console.print("[dim][OCR] 阶段 3/3：正在安全保存可搜索 PDF...[/dim]")
        check_cancelled()
        output_doc.save(temp_output, garbage=1, deflate=False)
        output_doc.close()
        source_doc.close()
        check_cancelled()
        os.replace(temp_output, output_pdf_path)
    except Exception as exc:
        return False, f"生成双层 PDF 时发生错误: {exc}"
    finally:
        if not output_doc.is_closed:
            output_doc.close()
        if not source_doc.is_closed:
            source_doc.close()
        if os.path.exists(temp_output):
            try:
                os.remove(temp_output)
            except OSError:
                pass

    replacement_note = (
        f"，并替换 {replaced_text_pages} 页旧扫描文字层"
        if replace_existing_scan_text and replaced_text_pages
        else ""
    )
    return True, (
        f"已处理 {total_pages} 页，其中 OCR 识别 {ocr_page_count} 页"
        f"{replacement_note}。可搜索 PDF 已生成。"
    )
