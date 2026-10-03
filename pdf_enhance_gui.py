"""PDF-Enhance 本地桌面界面：python pdf_enhance_gui.py。"""

import queue
import tempfile
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pymupdf as fitz

from pdf_enhance_core import (
    apply_bookmarks,
    detect_page_offset,
    detect_page_offset_with_ocr,
    detect_toc_pages,
    detect_toc_pages_with_ocr,
    generate_searchable_pdf,
    import_from_toc_file,
    load_general_config,
    parse_page_range,
    parse_toc_from_ocr_pages,
    parse_toc_from_pages,
    scan_layer_stats,
    text_layer_stats,
    text_quality_stats,
)

MODES = (
    "自动（扫描件按需 OCR）",
    "仅识别目录并制作书签",
    "补全缺少的文字层",
    "重做全文 OCR",
    "仅使用已有文字层",
)


class PDFEnhanceApp:
    def __init__(self, root):
        self.root = root
        root.title("PDF-Enhance · PDF 提升")
        root.geometry("850x680")
        root.minsize(650, 500)
        self.events = queue.Queue()
        self.source = None
        self.working = None
        self.items = []
        self.toc_pages = []
        self.use_ocr_coordinates = False
        self.busy = False

        frame = ttk.Frame(root, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(5, weight=1)

        ttk.Label(frame, text="PDF 文件").grid(row=0, column=0, sticky="w")
        self.path_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.path_var, state="readonly").grid(row=0, column=1, sticky="ew", padx=8)
        self.open_btn = ttk.Button(frame, text="选择…", command=self.choose_pdf)
        self.open_btn.grid(row=0, column=2)

        ttk.Label(frame, text="处理模式").grid(row=1, column=0, sticky="w", pady=8)
        self.mode_var = tk.StringVar(value=MODES[0])
        self.mode = ttk.Combobox(frame, textvariable=self.mode_var, values=MODES, state="readonly")
        self.mode.grid(row=1, column=1, sticky="ew", padx=8)
        self.mode.bind("<<ComboboxSelected>>", lambda _: self.clear_result())

        ttk.Label(frame, text="目录页码").grid(row=2, column=0, sticky="w")
        self.pages_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.pages_var).grid(row=2, column=1, sticky="ew", padx=8)
        ttk.Label(frame, text="留空自动查找；如 5-8").grid(row=2, column=2, sticky="w")

        self.analyze_btn = ttk.Button(frame, text="识别目录", command=self.analyze)
        self.analyze_btn.grid(row=3, column=0, pady=12, sticky="w")
        self.offset_var = tk.StringVar(value="0")
        ttk.Label(frame, text="页码偏移（可修改）").grid(row=3, column=1, sticky="e", padx=8)
        ttk.Entry(frame, textvariable=self.offset_var, width=8).grid(row=3, column=2, sticky="w")

        ttk.Label(frame, text="目录校对：每行标题 + Tab + 逻辑页码；用 Tab 缩进表示下级标题").grid(
            row=4, column=0, columnspan=3, sticky="w"
        )
        editor = ttk.Frame(frame)
        editor.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=5)
        editor.rowconfigure(0, weight=1)
        editor.columnconfigure(0, weight=1)
        self.toc_text = tk.Text(editor, wrap="none", undo=True)
        self.toc_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(editor, orient="vertical", command=self.toc_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.toc_text.configure(yscrollcommand=scrollbar.set)
        # Tab 应插入缩进，而非将焦点移走。
        self.toc_text.bind("<Tab>", lambda event: (self.toc_text.insert("insert", "\t"), "break")[1])

        self.save_btn = ttk.Button(frame, text="生成书签 PDF…", command=self.save, state="disabled")
        self.save_btn.grid(row=6, column=0, columnspan=3, sticky="ew", pady=8)
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=7, column=0, columnspan=3, sticky="ew")
        self.status_var = tk.StringVar(value="请选择 PDF 文件。")
        ttk.Label(frame, textvariable=self.status_var, wraplength=790).grid(
            row=8, column=0, columnspan=3, sticky="w", pady=8
        )
        root.after(100, self.poll_events)

    def clear_result(self):
        if self.busy:
            return
        self.working = None
        self.items = []
        self.toc_pages = []
        self.toc_text.delete("1.0", "end")
        self.save_btn.configure(state="disabled")

    def choose_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*")])
        if path:
            self.clear_result()
            self.source = Path(path)
            self.path_var.set(path)
            self.status_var.set("已选择 PDF；点击“识别目录”开始。")

    def set_busy(self, busy):
        self.busy = busy
        self.open_btn.configure(state="disabled" if busy else "normal")
        self.analyze_btn.configure(state="disabled" if busy else "normal")
        self.save_btn.configure(state="disabled" if busy or not self.items else "normal")
        self.mode.configure(state="disabled" if busy else "readonly")
        if busy:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)

    def start_job(self, target, *args):
        self.set_busy(True)
        threading.Thread(target=self.run_job, args=(target, *args), daemon=True).start()

    def run_job(self, target, *args):
        try:
            self.events.put(("done", target(*args)))
        except Exception as exc:
            self.events.put(("error", f"{type(exc).__name__}: {exc}"))

    def poll_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status_var.set(value)
                elif kind == "progress":
                    completed, total = value
                    self.progress.stop()
                    self.progress.configure(mode="determinate", maximum=max(1, total), value=completed)
                    self.status_var.set(f"OCR 识别中：{completed}/{total} 页")
                elif kind == "error":
                    self.set_busy(False)
                    self.status_var.set(value)
                    messagebox.showerror("处理失败", value)
                elif kind == "done":
                    self.set_busy(False)
                    if value[0] == "analysis":
                        _, working, items, pages, offset, detail, use_ocr = value
                        self.working = working
                        self.items = items
                        self.toc_pages = pages
                        self.use_ocr_coordinates = use_ocr
                        self.offset_var.set(str(offset))
                        self.toc_text.delete("1.0", "end")
                        for item in items:
                            indent = "\t" * (item.level - 1)
                            self.toc_text.insert("end", f"{indent}{item.title}\t{item.logical_page}\n")
                        self.save_btn.configure(state="normal")
                        self.status_var.set(f"识别到 {len(items)} 条目录，目录在第 {pages} 页。{detail}")
                    else:
                        self.status_var.set(value[1])
                        messagebox.showinfo("完成", value[1])
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def analyze(self):
        if not self.source:
            messagebox.showwarning("未选择文件", "请先选择 PDF 文件。")
            return
        if not self.source.is_file():
            messagebox.showerror("文件不存在", str(self.source))
            return
        mode = self.mode_var.get()
        searchable = self.source.with_name(self.source.stem + "_searchable.pdf")
        if mode in MODES[2:4] and searchable.exists():
            if not messagebox.askyesno("确认覆盖", f"以下文件已存在，成功处理后会替换它：\n{searchable}\n\n继续？"):
                return
        self.clear_result()
        self.start_job(self.analyze_worker, self.source, mode, self.pages_var.get(), searchable)

    def analyze_worker(self, source, mode, page_range, searchable):
        cfg = load_general_config()
        with fitz.open(source) as doc:
            total = len(doc)
            if page_range.strip() and not parse_page_range(page_range, total):
                raise ValueError("目录页码无效或超出 PDF 范围。")
            text_pages, _ = text_layer_stats(doc)
            suspicious, evaluated = text_quality_stats(doc)
            scan_pages, _ = scan_layer_stats(doc)
            scanned = (text_pages < total or scan_pages >= max(1, total // 2)
                       or (evaluated > 0 and suspicious >= max(2, int(evaluated * 0.2))))
        effective = (MODES[1] if scanned else MODES[4]) if mode == MODES[0] else mode
        working = source
        if effective in MODES[2:4]:
            self.events.put(("status", "正在生成可搜索 PDF…"))
            ok, message = generate_searchable_pdf(
                str(source), str(searchable), dpi=cfg["dpi"],
                max_workers=cfg["max_ocr_workers"],
                progress_callback=lambda done, total: self.events.put(("progress", (done, total))),
                skip_pages_with_text=effective == MODES[2],
                replace_existing_scan_text=effective == MODES[3],
            )
            if not ok:
                raise RuntimeError(message)
            working = searchable

        use_ocr = effective == MODES[1] and scanned
        self.events.put(("status", "正在寻找并解析目录…"))
        with fitz.open(working) as doc:
            if page_range.strip():
                pages = parse_page_range(page_range, len(doc))
                if not pages:
                    raise ValueError("目录页码无效或超出 PDF 范围。")
            else:
                pages = (detect_toc_pages_with_ocr(doc, dpi=min(cfg["dpi"], 180))
                         if scanned else detect_toc_pages(doc))
                if not pages:
                    raise ValueError("未自动找到目录页；请填写物理页码后重试。")
            items = (parse_toc_from_ocr_pages(doc, pages, dpi=cfg["dpi"])
                     if scanned and effective != MODES[4] else parse_toc_from_pages(doc, pages))
            if not items:
                raise ValueError("这些页面没有解析出目录条目；请更换目录页码。")
            if use_ocr:
                offset, detail = detect_page_offset_with_ocr(doc, items, pages[-1], dpi=min(cfg["dpi"], 180))
            else:
                offset, detail = detect_page_offset(doc, items, pages[-1])
        return "analysis", working, items, pages, offset, detail, use_ocr

    def save(self):
        if not self.working:
            return
        text = self.toc_text.get("1.0", "end")
        try:
            offset = int(self.offset_var.get().strip())
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "toc.txt"
                path.write_text(text, encoding="utf-8")
                items = import_from_toc_file(str(path))
            if not items:
                raise ValueError("目录不能为空。")
        except (ValueError, OSError) as exc:
            messagebox.showerror("目录格式错误", str(exc))
            return
        for index, item in enumerate(items):
            item.source_pdf_page = (self.items[index].source_pdf_page
                                    if index < len(self.items) and item.title == self.items[index].title
                                    else self.toc_pages[0])
        suggested = self.working.with_name(self.working.stem + "_bookmark.pdf")
        output = filedialog.asksaveasfilename(
            title="保存书签 PDF", initialdir=str(suggested.parent), initialfile=suggested.name,
            defaultextension=".pdf", filetypes=[("PDF 文件", "*.pdf")], confirmoverwrite=False,
        )
        if not output:
            return
        output = Path(output)
        if output.exists() and not messagebox.askyesno("确认覆盖", f"将替换已有文件：\n{output}\n\n继续？"):
            return
        self.start_job(self.save_worker, self.working, output, items, offset, self.use_ocr_coordinates)

    def save_worker(self, working, output, items, offset, use_ocr):
        self.events.put(("status", "正在写入书签…"))
        ok, count, message = apply_bookmarks(
            str(working), str(output), items, page_offset=offset,
            use_ocr_coordinates=use_ocr,
            ocr_dpi=min(load_general_config()["dpi"], 180),
        )
        if not ok:
            raise RuntimeError(message)
        return "saved", f"{message}\n输出：{output}"


def main():
    root = tk.Tk()
    PDFEnhanceApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
