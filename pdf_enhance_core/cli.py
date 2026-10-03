import sys
import os
from multiprocessing import cpu_count
import pymupdf as fitz
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.tree import Tree

from pdf_enhance_core import (
    text_layer_stats,
    text_quality_stats,
    scan_layer_stats,
    detect_toc_pages,
    detect_toc_pages_with_ocr,
    parse_page_range,
    parse_toc_from_pages,
    parse_toc_from_ocr_pages,
    detect_page_offset,
    detect_page_offset_with_ocr,
    export_to_toc_file,
    import_from_toc_file,
    apply_bookmarks,
    generate_searchable_pdf,
    parse_toc_with_vision_ai,
    load_ai_config,
    load_general_config,
    TOCItem
)
from pdf_enhance_core.ocr_engine import set_process_low_priority

# 降低后台 OCR 进程优先级，减少对前台操作的影响
set_process_low_priority()

APP_NAME = "PDF-Enhance"
APP_NAME_ZH = "PDF 增强"
APP_VERSION = "0.1.0"

console = Console()

def print_banner():
    console.print(Panel.fit(
        f"[bold cyan]{APP_NAME}[/bold cyan] [dim]v{APP_VERSION}[/dim]\n"
        "[dim]OCR、目录识别和书签制作[/dim]",
        border_style="cyan"
    ))

def select_performance_profile(configured_workers: int | None = None) -> int:
    """交互选择算力挡位，配置文件中的核心数作为均衡模式默认值。"""
    total_cpus = cpu_count()
    balanced = max(1, min(configured_workers or max(2, total_cpus // 2), total_cpus))
    extreme = max(2, total_cpus - 2)
    eco = max(1, min(2, total_cpus // 4))

    console.print(Panel(
        f"电脑共有 {total_cpus} 个逻辑核心。\n\n"
        f" [bold green][1][/bold green] 均衡：使用 {balanced} 个核心\n"
        f" [bold yellow][2][/bold yellow] 全速：使用 {extreme} 个核心\n"
        f" [bold cyan][3][/bold cyan] 低占用：使用 {eco} 个核心\n"
        f" [bold blue][4][/bold blue] 自定义",
        title="OCR 性能",
        border_style="cyan"
    ))

    choice = Prompt.ask("[bold]请选择档位编号[/bold]", choices=["1", "2", "3", "4"], default="1")
    if choice == "1":
        return balanced
    elif choice == "2":
        return extreme
    elif choice == "3":
        return eco
    elif choice == "4":
        custom = IntPrompt.ask(f"[bold yellow]请输入期望使用的核心数 (1 ~ {total_cpus})[/bold yellow]", default=balanced)
        return max(1, min(custom, total_cpus))
    return balanced

def display_toc_preview(toc_items: list[TOCItem], offset: int, total_pages: int):
    """在终端美观地打印出解析出的书签树"""
    tree = Tree(f"[bold yellow]书签预览：{len(toc_items)} 条，页码偏移 {offset:+}[/bold yellow]")
    
    current_parents = {0: tree}
    
    for idx, item in enumerate(toc_items):
        target_p = item.logical_page + offset
        color = "green" if item.level == 1 else ("blue" if item.level == 2 else "magenta")
        if target_p < 1 or target_p > total_pages:
            target_text = f"[bold red]{target_p}（超出范围）[/bold red]"
        else:
            target_text = f"[bold]{target_p}[/bold]"
        label = f"[{color}][L{item.level}] {item.title}[/{color}] [dim](逻辑页: {item.logical_page} -> 物理页: {target_text})[/dim]"
        
        parent_level = item.level - 1
        while parent_level not in current_parents and parent_level > 0:
            parent_level -= 1
        parent_node = current_parents.get(parent_level, tree)
        
        node = parent_node.add(label)
        current_parents[item.level] = node
        
    console.print(tree)

def run_wizard():
    if len(sys.argv) > 1 and sys.argv[1] in {"-h", "--help"}:
        print(
            "PDF-Enhance：本地 PDF OCR、目录解析与精确书签工具\n\n"
            "用法：\n"
            "  pdf-enhance [PDF文件路径]\n"
            "  python pdf_enhance.py [PDF文件路径]\n\n"
            "选项：\n"
            "  -h, --help       显示帮助\n"
            "  -V, --version    显示版本"
        )
        return
    if len(sys.argv) > 1 and sys.argv[1] in {"-V", "--version"}:
        print(f"{APP_NAME} {APP_VERSION}")
        return

    print_banner()
    
    # 1. 获取输入 PDF 路径
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1].strip('\"\'')
    else:
        pdf_path = Prompt.ask("\n[bold yellow]请输入或拖入 PDF 文件路径[/bold yellow]").strip('\"\'')

    if not os.path.isfile(pdf_path):
        console.print(f"[bold red]找不到文件：{pdf_path}[/bold red]")
        return

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        console.print(f"[bold red]无法打开 PDF：{e}[/bold red]")
        return

    total_pages = len(doc)
    console.print(f"[green]已打开 PDF，共 {total_pages} 页。[/green]")

    # 2. 检查文本层并加载通用配置
    general_cfg = load_general_config()
    ocr_dpi = general_cfg["dpi"]
    configured_workers = general_cfg["max_ocr_workers"]
    text_pages, total_text_check_pages = text_layer_stats(doc)
    suspicious_text_pages, evaluated_text_pages = text_quality_stats(doc)
    scan_like_pages, invisible_text_pages = scan_layer_stats(doc)
    has_text = text_pages == total_text_check_pages
    damaged_text_layer = (
        evaluated_text_pages > 0
        and suspicious_text_pages >= max(2, int(evaluated_text_pages * 0.2))
    )
    source_was_scanned = (
        text_pages < total_text_check_pages
        or scan_like_pages >= max(1, total_text_check_pages // 2)
        or damaged_text_layer
    )
    metadata_text = " ".join(
        str(value or "") for value in (doc.metadata or {}).values()
    ).lower()
    legacy_searchable = (
        os.path.splitext(pdf_path)[0].lower().endswith("_searchable")
        and invisible_text_pages >= max(1, int(total_pages * 0.8))
    )
    already_processed = "pdf-enhance searchable ocr" in metadata_text or legacy_searchable
    fast_scan_mode = source_was_scanned and already_processed
    working_pdf_path = pdf_path

    if invisible_text_pages:
        console.print(
            f"[yellow]检测到 {invisible_text_pages} 页隐藏文字。"
            "它可能是 OCR 文字层，也可能是文字水印。[/yellow]"
        )

    if source_was_scanned and already_processed:
        console.print("[green]该文件已经过 PDF-Enhance 处理，跳过 OCR。[/green]")

    if source_was_scanned and not already_processed:
        if text_pages == 0:
            document_kind = "纯图片 PDF（没有文字层）"
        elif damaged_text_layer:
            document_kind = (
                f"PDF 有文字层，但 {suspicious_text_pages}/{evaluated_text_pages} 页疑似乱码"
            )
        elif text_pages < total_text_check_pages:
            document_kind = (
                f"部分页面没有文字层（已有 {text_pages}/{total_text_check_pages} 页）"
            )
        else:
            document_kind = (
                f"扫描型 PDF（全部页面都有文字层，其中 {invisible_text_pages} 页含隐藏文字）"
            )

        if 0 < text_pages < total_text_check_pages:
            menu = (
                " [bold green][1][/bold green] [bold]强制重新 OCR 全部页面[/bold]：\n"
                f"     对全部 {total_pages} 页重新识别，适合已有文字乱码、搜索错误或质量较差的文件。\n"
                " [bold yellow][2][/bold yellow] [bold]仅补全缺少文字层的页面[/bold]：\n"
                f"     保留已有文字层，只 OCR 其余 {total_pages - text_pages} 页。\n"
                " [bold cyan][3][/bold cyan] [bold]轻量书签模式[/bold]：\n"
                "     不生成全文文字层，仅按需识别目录和章节目标页。\n"
                " [bold red][0][/bold red] 退出"
            )
            choices = ["1", "2", "3", "0"]
            supplement_choice = "2"
            light_choice = "3"
        elif text_pages == 0:
            menu = (
                " [bold green][1][/bold green] [bold]全书 OCR + 制作书签[/bold]\n"
                " [bold cyan][2][/bold cyan] [bold]轻量书签模式（不生成全文文字层）[/bold]\n"
                " [bold red][0][/bold red] 退出"
            )
            choices = ["1", "2", "0"]
            supplement_choice = None
            light_choice = "2"
        else:
            menu = (
                " [bold green][1][/bold green] [bold]强制重新 OCR 全部页面[/bold]：\n"
                "     适合现有隐藏文字乱码、无法搜索或高亮错位。\n"
                " [bold cyan][2][/bold cyan] [bold]保留现有文字层，仅按需制作书签[/bold]\n"
                " [bold red][0][/bold red] 退出"
            )
            choices = ["1", "2", "0"]
            supplement_choice = None
            light_choice = "2"

        console.print(Panel(
            f"[bold yellow]{document_kind}[/bold yellow]\n\n"
            "有文字层不代表文字正确，乱码也会被计入。\n"
            "选择“强制重新 OCR”时，会清除全页扫描图上的旧 PDF 文字层，"
            "其中的文字水印也会清除。印在图片里的水印不会清除。\n"
            "请选择：\n" + menu,
            title="扫描 PDF",
            border_style="yellow"
        ))

        scan_choice = Prompt.ask(
            "[bold]请选择模式编号[/bold]", choices=choices, default="1"
        )
        if scan_choice == "0":
            doc.close()
            return

        fast_scan_mode = scan_choice == light_choice
        should_generate_searchable = scan_choice == "1" or scan_choice == supplement_choice
        if should_generate_searchable:
            force_all_pages = scan_choice == "1"
            workers = select_performance_profile(configured_workers)
            searchable_output = f"{os.path.splitext(pdf_path)[0]}_searchable.pdf"
            mode_text = "全部页面" if force_all_pages else "缺少文字层的页面"
            console.print(
                f"\n[cyan]开始 OCR：识别{mode_text}，使用 {workers} 个进程。[/cyan]"
            )
            doc.close()

            ok, msg = generate_searchable_pdf(
                pdf_path,
                searchable_output,
                dpi=ocr_dpi,
                max_workers=workers,
                skip_pages_with_text=not force_all_pages,
                replace_existing_scan_text=force_all_pages,
            )
            if not ok:
                console.print(f"[bold red]OCR 失败：{msg}[/bold red]")
                return
            console.print(f"[bold green]{msg}[/bold green]")
            working_pdf_path = searchable_output
            doc = fitz.open(working_pdf_path)

    # 3. 确定目录页码
    toc_pages = []
    while not toc_pages:
        user_range = Prompt.ask(
            "\n[bold yellow]目录所在的物理页码[/bold yellow]（如 [cyan]5-8[/cyan] 或 [cyan]5,6,7[/cyan]）\n"
            "[dim]直接回车：自动查找[/dim]", 
            default=""
        )

        if user_range.strip():
            parsed_pages = parse_page_range(user_range, total_pages)
            if parsed_pages:
                toc_pages = parsed_pages
                console.print(f"[green]使用目录页：{toc_pages}[/green]")
            else:
                console.print("[bold red]页码格式不对，请重新输入。[/bold red]")
        else:
            with console.status("[cyan]正在查找目录页...[/cyan]"):
                detected = (
                    detect_toc_pages_with_ocr(doc, dpi=min(ocr_dpi, 180))
                    if source_was_scanned
                    else detect_toc_pages(doc)
                )
            if detected:
                toc_pages = detected
                console.print(f"[green]找到目录页：{toc_pages}[/green]")
            else:
                console.print("[yellow]没有找到目录页，请手动输入页码（如 2 或 5-8）。[/yellow]")

    # 4. 解析目录与计算偏移量
    toc_items = []
    offset = 0
    ai_cfg = load_ai_config()

    def parse_and_calc(pages: list[int]):
        nonlocal toc_items, offset
        
        # 如果启用了 AI 视觉大模型
        if ai_cfg.get("enabled", False) and ai_cfg.get("api_key"):
            with console.status(f"[cyan]正在用 {ai_cfg.get('model')} 读取目录...[/cyan]"):
                ai_items = parse_toc_with_vision_ai(doc, pages, ai_cfg)
            if ai_items:
                toc_items = ai_items
                console.print("[green]目录读取完成。[/green]")
            else:
                console.print("[yellow]在线模型没有返回有效目录，改用本地 OCR。[/yellow]")
                with console.status("[cyan]正在读取目录...[/cyan]"):
                    toc_items = (
                        parse_toc_from_ocr_pages(doc, pages, dpi=ocr_dpi)
                        if source_was_scanned
                        else parse_toc_from_pages(doc, pages)
                    )
        else:
            if source_was_scanned:
                with console.status("[cyan]正在读取目录标题和页码...[/cyan]"):
                    toc_items = parse_toc_from_ocr_pages(doc, pages, dpi=ocr_dpi)
            else:
                with console.status("[cyan]正在读取目录...[/cyan]"):
                    toc_items = parse_toc_from_pages(doc, pages)
                
        if not toc_items:
            return False
            
        with console.status("[cyan]正在计算页码偏移...[/cyan]"):
            if fast_scan_mode:
                offset, desc = detect_page_offset_with_ocr(
                    doc, toc_items, pages[-1], dpi=min(ocr_dpi, 180)
                )
            else:
                offset, desc = detect_page_offset(doc, toc_items, pages[-1])
        console.print(f"[cyan]{desc}[/cyan]")
        return True

    success = parse_and_calc(toc_pages)
    if not success:
        console.print(Panel(
            "[bold red]没有读到目录条目。[/bold red]\n"
            "请检查目录页码。",
            border_style="red"
        ))

    # 5. 交互循环菜单
    default_output_pdf = f"{os.path.splitext(working_pdf_path)[0]}_bookmark.pdf"
    default_toc_txt = f"{os.path.splitext(working_pdf_path)[0]}_toc.txt"

    while True:
        if toc_items:
            console.print("\n")
            display_toc_preview(toc_items, offset, total_pages)
        else:
            console.print("\n[yellow]（暂无可用书签数据）[/yellow]")

        console.print("\n[bold]下一步：[/bold]")
        console.print(" [bold green][1][/bold green] 生成书签 PDF")
        console.print(" [bold cyan][2][/bold cyan] 导出 toc.txt 后校对")
        console.print(f" [bold yellow][3][/bold yellow] 修改页码偏移（当前 {offset:+}）")
        console.print(" [bold blue][4][/bold blue] 重新选择目录页")
        console.print(" [bold red][0][/bold red] 退出")

        choice = Prompt.ask("\n[bold]请输入选项编号[/bold]", choices=["1", "2", "3", "4", "0"], default="1" if toc_items else "4")

        if choice == "1":
            if not toc_items:
                console.print("[red]没有可写入的书签，请先检查目录页。[/red]")
                continue
            
            special_titles = {"目次", "目录", "前言", "序言", "编制说明"}
            valid_items = []
            invalid_items = []
            for item in toc_items:
                target_page = item.logical_page + offset
                if item.title.strip() in special_titles or 1 <= target_page <= total_pages:
                    valid_items.append(item)
                else:
                    invalid_items.append((item, target_page))

            items_to_write = toc_items
            if invalid_items:
                examples = "，".join(
                    f"{item.title} -> {target}" for item, target in invalid_items[:5]
                )
                console.print(
                    f"[yellow]有 {len(invalid_items)} 个目录条目指向 PDF 之外：{examples}。[/yellow]\n"
                    f"当前文件只有 {total_pages} 个物理页，文件可能不完整，"
                    "也可能是目录页码识别有误。"
                )
                if not valid_items:
                    console.print("[red]没有可写入的有效书签。[/red]")
                    continue
                if not Confirm.ask(
                    f"是否只生成范围内的 {len(valid_items)} 个书签",
                    default=True,
                ):
                    continue
                items_to_write = valid_items

            output_file = Prompt.ask("[bold]输出 PDF 文件路径[/bold]", default=default_output_pdf)
            with console.status("[cyan]正在写入书签...[/cyan]"):
                ok, count, msg = apply_bookmarks(
                    working_pdf_path,
                    output_file,
                    items_to_write,
                    page_offset=offset,
                    use_ocr_coordinates=fast_scan_mode,
                    ocr_dpi=min(ocr_dpi, 180),
                )
            if ok:
                console.print(Panel(
                    f"[green]{msg}[/green]\n\n"
                    f"输出文件：[cyan]{os.path.abspath(output_file)}[/cyan]",
                    title="完成",
                    border_style="green"
                ))
                break
            else:
                console.print(f"[bold red]{msg}[/bold red]")

        elif choice == "2":
            if not toc_items:
                console.print("[red]没有可导出的书签。[/red]")
                continue

            txt_file = Prompt.ask("[bold]导出 txt 文件路径[/bold]", default=default_toc_txt)
            export_to_toc_file(toc_items, txt_file)
            console.print(Panel(
                f"文件：[cyan]{os.path.abspath(txt_file)}[/cyan]\n\n"
                "可修改标题、页码和缩进层级。保存后回到这里按回车。",
                title="toc.txt 已导出",
                border_style="cyan"
            ))
            
            Prompt.ask("[bold yellow]修改完成后，请按 Enter 回车以重新加载 txt 并写入 PDF[/bold yellow]", default="")
            
            try:
                imported_items = import_from_toc_file(txt_file)
                if imported_items:
                    toc_items = imported_items
                    console.print(f"[green]已读取 {len(toc_items)} 条书签。[/green]")
                else:
                    console.print("[yellow]文件中没有有效书签。[/yellow]")
            except Exception as e:
                console.print(f"[red]读取 toc.txt 失败：{e}[/red]")

        elif choice == "3":
            new_offset = IntPrompt.ask("[bold yellow]请输入新的页码偏移量 (整数，可正可负)[/bold yellow]", default=offset)
            offset = new_offset
            console.print(f"[green]页码偏移已改为 {offset:+}。[/green]")

        elif choice == "4":
            new_range = Prompt.ask("[bold yellow]请输入新的目录页码范围 (如 2 或 5-8)[/bold yellow]")
            parsed_pages = parse_page_range(new_range, total_pages)
            if parsed_pages:
                toc_pages = parsed_pages
                parse_and_calc(toc_pages)
            else:
                console.print("[red]页码格式不对，没有修改。[/red]")

        elif choice == "0":
            console.print("[dim]已退出程序。[/dim]")
            break

    doc.close()

