"""Windows 桌面界面：三个任务，共用现有 OCR 和书签处理模块。"""

import ctypes
import os
import queue
import shutil
import tempfile
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk

import pymupdf as fitz
from pdf_enhance_core import (
    apply_bookmarks, detect_page_offset, detect_page_offset_with_ocr,
    detect_toc_pages_with_ocr, generate_searchable_pdf,
    import_from_toc_file, load_general_config,
    parse_toc_from_ocr_pages, parse_toc_from_pages,
)
from pdf_enhance_core.detector import usable_page_text
from pdf_enhance_core.parser import guess_level_by_numbering, toc_items_need_ocr

MODES = ("仅全文 OCR", "全文 OCR + 制作书签", "仅制作书签")

def output_path(source, directory, mode):
    suffix = ("_ocr", "_ocr_bookmark", "_bookmark")[MODES.index(mode)]
    return Path(directory) / (Path(source).stem + suffix + ".pdf")


def export_ocr_fallback(working, output):
    """目录失败后才导出临时 OCR，原子保存以保护已有文件。"""
    fd, temporary = tempfile.mkstemp(prefix=".pdf-enhance-ocr-", suffix=".pdf", dir=Path(output).parent)
    os.close(fd)
    try:
        shutil.copyfile(working, temporary)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def display_path(path):
    """Tk 的文件对话框使用正斜杠；统一为当前系统原生路径。"""
    return os.path.normpath(str(path))


def enable_high_dpi():
    """在创建任何窗口前启用 DPI 感知；冻结版亦通过 manifest 声明。"""
    if os.name == "nt":
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (AttributeError, OSError):
                ctypes.windll.user32.SetProcessDPIAware()


class PDFEnhanceApp:
    def __init__(self, root):
        self.root = root
        root.title("PDF-Enhance · PDF 增强")
        scale = root.winfo_fpixels("1i") / 96
        width = min(round(960 * scale), round(root.winfo_screenwidth() * .92))
        height = min(round(780 * scale), round(root.winfo_screenheight() * .85))
        root.geometry(f"{width}x{height}")
        root.minsize(min(width, round(680 * scale)), min(height, round(580 * scale)))
        if os.name == "nt":
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
                font.nametofont(name).configure(family="Microsoft YaHei UI", size=10)
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Action.TButton", padding=(12, 7))
        self.events = queue.Queue()
        self.source = None
        self.working = None
        self.items = []
        self.toc_pages = []
        self.use_ocr_coordinates = False
        self.ocr_cache = {}
        self.busy = False
        self.workspace = tempfile.TemporaryDirectory(prefix="pdf-enhance-gui-")
        self.controls = []
        cfg = load_general_config()
        self.cpu_count = os.cpu_count() or 1

        self.path_var = tk.StringVar()
        self.directory_var = tk.StringVar()
        self.mode_var = tk.StringVar(value=MODES[1])
        self.page_offset = None
        self.workers_var = tk.StringVar(value=str(min(cfg["max_ocr_workers"], self.cpu_count)))
        self.status_var = tk.StringVar(value="请选择 PDF 文件和输出目录。")

        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk.Label(frame, text="PDF 增强", style="Title.TLabel").grid(row=0, sticky="w", pady=(0, 10))
        files = ttk.LabelFrame(frame, text="1. 文件与任务", padding=10)
        files.grid(row=1, sticky="ew")
        files.columnconfigure(1, weight=1)
        for row, label, var, command in (
            (0, "输入 PDF", self.path_var, self.choose_pdf),
            (1, "输出目录", self.directory_var, self.choose_directory),
        ):
            ttk.Label(files, text=label).grid(row=row, column=0, sticky="w", pady=5)
            entry = ttk.Entry(files, textvariable=var, state="readonly")
            entry.grid(row=row, column=1, sticky="ew", padx=10)
            button = ttk.Button(files, text="选择…", command=command)
            button.grid(row=row, column=2)
            self.controls.append((button, "normal"))
        ttk.Label(files, text="任务").grid(row=2, column=0, sticky="nw", pady=7)
        choices = ttk.Frame(files)
        choices.grid(row=2, column=1, columnspan=2, sticky="w", padx=10)
        for index, mode in enumerate(MODES):
            button = ttk.Radiobutton(choices, text=mode, value=mode, variable=self.mode_var, command=self.mode_changed)
            button.grid(row=index, sticky="w", pady=2)
            self.controls.append((button, "normal"))

        options = ttk.Frame(frame, padding=(0, 10))
        options.grid(row=2, sticky="ew")
        ttk.Label(options, text="OCR 并发进程数").pack(side="left")
        workers = ttk.Spinbox(options, from_=1, to=self.cpu_count, textvariable=self.workers_var, width=5)
        workers.pack(side="left", padx=8)
        self.controls.append((workers, "normal"))
        ttk.Label(options, text=f"共 {self.cpu_count} 个逻辑核心；数值越大，占用越高").pack(side="left")

        self.bookmarks = ttk.LabelFrame(frame, text="2. 目录与书签校对", padding=10)
        self.bookmarks.grid(row=3, sticky="nsew")
        self.bookmarks.columnconfigure(0, weight=1)
        self.bookmarks.rowconfigure(1, weight=1)
        ttk.Label(self.bookmarks, text="每行：标题 + Tab + 页码；Tab 缩进表示下级标题。识别后可直接修改。",
                  wraplength=600).grid(row=0, sticky="w", pady=5)
        editor = ttk.Frame(self.bookmarks)
        editor.grid(row=1, sticky="nsew")
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(0, weight=1)
        self.toc_text = tk.Text(editor, wrap="none", undo=True, height=7, width=40, font="TkTextFont")
        self.toc_text.grid(row=0, column=0, sticky="nsew")
        for orient, command, row, column, sticky in (
            ("vertical", self.toc_text.yview, 0, 1, "ns"),
            ("horizontal", self.toc_text.xview, 1, 0, "ew"),
        ):
            bar = ttk.Scrollbar(editor, orient=orient, command=command)
            bar.grid(row=row, column=column, sticky=sticky)
            self.toc_text.configure(**{("yscrollcommand" if orient == "vertical" else "xscrollcommand"): bar.set})
        self.toc_text.bind("<Tab>", lambda _: (self.toc_text.insert("insert", "\t"), "break")[1])

        actions = ttk.Frame(frame, padding=(0, 10))
        actions.grid(row=4, sticky="ew")
        self.start_btn = ttk.Button(actions, style="Action.TButton", command=self.start)
        self.start_btn.pack(side="left")
        self.save_btn = ttk.Button(actions, text="确认目录并生成 PDF", style="Action.TButton", command=self.save)
        self.save_btn.pack(side="right")
        self.progress = ttk.Progressbar(frame, mode="determinate")
        self.progress.grid(row=5, sticky="ew")
        self.status_label = ttk.Label(frame, textvariable=self.status_var, wraplength=700)
        self.status_label.grid(row=6, sticky="ew", pady=(8, 0))
        frame.bind("<Configure>", lambda event: self.resize_labels(event.width))
        self.mode_changed()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll_events)

    def resize_labels(self, width):
        self.status_label.configure(wraplength=max(200, width - 50))

    def clear_result(self):
        self.working = None
        self.items = []
        self.toc_pages = []
        self.page_offset = None
        self.ocr_cache = {}
        self.toc_text.configure(state="normal")
        self.toc_text.delete("1.0", "end")
        self.save_btn.configure(state="disabled")

    def mode_changed(self):
        self.clear_result()
        index = MODES.index(self.mode_var.get())
        self.start_btn.configure(text=("开始全文 OCR" if index == 0 else
                                      "开始 OCR 并识别目录" if index == 1 else "识别目录与书签"))
        if index == 0:
            self.bookmarks.grid_remove()
            self.save_btn.pack_forget()
        else:
            self.bookmarks.grid()
            self.save_btn.pack(side="right")

    def choose_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("PDF 文件", "*.pdf")])
        if path:
            self.source = Path(path)
            self.path_var.set(display_path(path))
            if not self.directory_var.get():
                self.directory_var.set(display_path(self.source.parent))
            self.clear_result()
            self.status_var.set("已选择文件；设置任务后点击开始。")

    def choose_directory(self):
        path = filedialog.askdirectory(title="选择输出目录", initialdir=self.directory_var.get() or None)
        if path:
            self.directory_var.set(display_path(path))

    def set_busy(self, busy):
        self.busy = busy
        for widget, state in self.controls:
            widget.configure(state="disabled" if busy else state)
        self.toc_text.configure(state="disabled" if busy else "normal")
        self.start_btn.configure(state="disabled" if busy else "normal")
        self.save_btn.configure(state="disabled" if busy or not self.items else "normal")
        self.progress.stop()
        self.progress.configure(mode="indeterminate" if busy else "determinate", value=0)
        if busy:
            self.progress.start(12)

    def start_job(self, target, *args):
        self.set_busy(True)
        threading.Thread(target=self.run_job, args=(target, *args), daemon=True).start()

    def run_job(self, target, *args):
        try:
            self.events.put(("done", target(*args)))
        except Exception as exc:
            self.events.put(("error", f"错误：{exc}"))

    def poll_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.progress.stop()
                    self.progress.configure(mode="indeterminate")
                    self.progress.start(12)
                    self.status_var.set(value)
                elif kind == "progress":
                    done, total = value
                    self.progress.stop()
                    self.progress.configure(mode="determinate", maximum=max(1, total), value=done)
                    self.status_var.set(f"全文 OCR：{done}/{total} 页")
                elif kind == "error":
                    self.set_busy(False)
                    self.status_var.set(value)
                    messagebox.showerror("处理失败", value)
                elif kind == "done":
                    self.set_busy(False)
                    if value[0] == "analysis":
                        _, self.working, self.items, self.toc_pages, offset, detail, self.use_ocr_coordinates, self.ocr_cache = value
                        self.page_offset = offset
                        self.toc_text.delete("1.0", "end")
                        for item in self.items:
                            indent = "\t" * (item.level - 1)
                            self.toc_text.insert("end", f"{indent}{item.title}\t{item.logical_page}\n")
                        self.save_btn.configure(state="normal")
                        self.status_var.set(f"识别到 {len(self.items)} 条目录，请校对后生成 PDF。")
                    else:
                        self.status_var.set(value[1])
                        if value[0] == "ocr_only":
                            self.save_fallback(value[2], value[1])
                        elif value[0] == "ocr_saved":
                            messagebox.showwarning("OCR 已完成，书签未生成", value[1])
                        else:
                            self.working = None
                            self.ocr_cache = {}
                            self.items = []
                            self.save_btn.configure(state="disabled")
                            messagebox.showinfo("完成", value[1])
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def validate_target(self, mode=None):
        if not self.source or not self.source.is_file():
            raise ValueError("请先选择存在的 PDF 文件。")
        directory = Path(self.directory_var.get()) if self.directory_var.get() else None
        if directory is None or not directory.is_dir():
            raise ValueError("请选择有效的输出目录。")
        target = output_path(self.source, directory, mode or self.mode_var.get())
        if target.resolve() == self.source.resolve() or (target.exists() and os.path.samefile(target, self.source)):
            raise ValueError("不能覆盖输入文件，请更换输出目录。")
        return target

    def confirm_target(self, target):
        return not target.exists() or messagebox.askyesno("确认覆盖", f"生成成功后将替换：\n{display_path(target)}\n\n继续？")

    def start(self):
        try:
            target = self.validate_target()
            try:
                workers = int(self.workers_var.get())
            except ValueError:
                raise ValueError("OCR 并发进程数必须是整数。") from None
            if not 1 <= workers <= self.cpu_count:
                raise ValueError(f"OCR 并发进程数必须在 1 到 {self.cpu_count} 之间。")
        except ValueError as exc:
            messagebox.showerror("设置错误", str(exc))
            return
        mode = self.mode_var.get()
        if mode == MODES[0] and not self.confirm_target(target):
            return
        if mode != MODES[2] and not messagebox.askyesno(
            "全文 OCR", "将重新识别全部页面。全页扫描图上的旧文字层会被移除，包括文字水印；原始 PDF 不会修改。\n\n继续？"
        ):
            return
        self.clear_result()
        working = Path(self.workspace.name) / "searchable.pdf" if mode == MODES[1] else target
        self.start_job(self.process_worker, self.source, mode, working, workers)

    def process_worker(self, source, mode, searchable, workers):
        cfg = load_general_config()
        ocr_cache = {}
        with fitz.open(source) as doc:
            if doc.needs_pass:
                raise ValueError("请先解密 PDF 后再处理。")
            if not len(doc):
                raise ValueError("PDF 没有可处理的页面。")
        working = source
        if mode != MODES[2]:
            self.events.put(("status", f"正在对全部页面执行 OCR，使用 {workers} 个并发进程…"))
            ok, message = generate_searchable_pdf(
                str(source), str(searchable), dpi=cfg["dpi"], max_workers=workers,
                progress_callback=lambda done, total: self.events.put(("progress", (done, total))),
                skip_pages_with_text=False, replace_existing_scan_text=True,
                ocr_page_callback=(lambda index, items: ocr_cache.__setitem__(index, items)) if mode == MODES[1] else None,
            )
            if not ok:
                raise RuntimeError(message)
            if mode == MODES[0]:
                return "saved", f"{message}\n输出：{display_path(searchable)}"
            working = searchable

        try:
            self.events.put(("status", "正在自动寻找目录、解析书签并计算页码偏移…"))
            with fitz.open(working) as doc:
                # 不看扫描底图比例：逐页先用正常文字层；新生成的 OCR 则直接复用。
                pages = detect_toc_pages_with_ocr(doc, dpi=cfg["dpi"], ocr_cache=ocr_cache, prefer_text=True)
                if not pages:
                    # 文字可读不等于排版能被解析，整个目录探测失败后才强制 OCR 重试。
                    pages = detect_toc_pages_with_ocr(doc, dpi=cfg["dpi"], ocr_cache=ocr_cache)
                if not pages:
                    raise ValueError("未自动找到目录页")
                items = []
                for number in pages:
                    parsed = []
                    if number - 1 not in ocr_cache and usable_page_text(doc[number - 1]):
                        try:
                            parsed = parse_toc_from_pages(doc, [number])
                        except (ValueError, RuntimeError):
                            parsed = []
                    if toc_items_need_ocr(parsed):
                        parsed = parse_toc_from_ocr_pages(doc, [number], dpi=cfg["dpi"], ocr_cache=ocr_cache)
                    if not parsed:
                        raise ValueError(f"第 {number} 页目录没有解析出有效书签条目")
                    if items:
                        parsed[0].level = guess_level_by_numbering(parsed[0].title) or parsed[0].level
                    items.extend(parsed)
                items[0].level = 1
                for index in range(1, len(items)):
                    items[index].level = max(1, min(items[index].level, items[index - 1].level + 1))
                try:
                    offset, detail = detect_page_offset(doc, items, pages[-1], require_match=True)
                except ValueError:
                    offset, detail = detect_page_offset_with_ocr(
                        doc, items, pages[-1], dpi=cfg["dpi"], ocr_cache=ocr_cache, require_match=True,
                    )
            # 定位时也先使用可用文字层，缺失或未匹配才局部 OCR，不能整本强制 OCR。
            return "analysis", working, items, pages, offset, detail, False, ocr_cache
        except Exception as exc:
            if mode == MODES[1]:
                return "ocr_only", f"错误：目录或书签识别失败（{exc}）。", searchable
            raise

    def save_fallback(self, working, reason):
        try:
            output = self.validate_target(MODES[0])
        except ValueError as exc:
            messagebox.showerror("OCR 导出失败", f"{reason}\n{exc}")
            return
        if self.confirm_target(output):
            self.start_job(self.fallback_worker, working, output, reason)
        else:
            self.status_var.set(f"{reason} 未覆盖已有 OCR 文件。")

    def fallback_worker(self, working, output, reason):
        export_ocr_fallback(working, output)
        return "ocr_saved", f"{reason}\n全文 OCR 已完成并保存：{display_path(output)}"

    def save(self):
        if not self.working:
            return
        try:
            target = self.validate_target()
            offset = self.page_offset
            if offset is None:
                raise ValueError("尚未完成自动页码偏移识别。")
            path = Path(self.workspace.name) / "toc.txt"
            path.write_text(self.toc_text.get("1.0", "end"), encoding="utf-8")
            items = import_from_toc_file(str(path))
            if not items or any(not item.title or item.logical_page < 1 for item in items):
                raise ValueError("目录不能为空，标题不能为空，页码必须为正整数。")
        except (ValueError, OSError) as exc:
            messagebox.showerror("设置或目录格式错误", str(exc))
            return
        for item in items:
            original = next((entry for entry in self.items if entry.title == item.title), None)
            item.source_pdf_page = original.source_pdf_page if original else self.toc_pages[0]
        if self.confirm_target(target):
            self.start_job(self.save_worker, self.working, target, items, offset, self.use_ocr_coordinates, self.ocr_cache)

    def save_worker(self, working, output, items, offset, use_ocr, ocr_cache=None):
        self.events.put(("status", "正在定位标题并生成最终 PDF…"))
        ok, _, message = apply_bookmarks(
            str(working), str(output), items, page_offset=offset,
            use_ocr_coordinates=use_ocr, ocr_dpi=load_general_config()["dpi"], ocr_cache=ocr_cache,
            ocr_fallback=True,
        )
        if not ok:
            raise RuntimeError(message)
        return "saved", f"{message}\n输出：{display_path(output)}"

    def close(self):
        if self.busy:
            messagebox.showwarning("正在处理", "请等待当前任务结束后关闭窗口，以免中断处理。")
            return
        self.workspace.cleanup()
        self.root.destroy()


def main():
    enable_high_dpi()
    root = tk.Tk()
    PDFEnhanceApp(root)
    root.mainloop()


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main()
