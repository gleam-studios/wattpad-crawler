#!/usr/bin/env python3

import argparse
import contextlib
import io
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import traceback
import webbrowser
import zipfile
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from wattpad_tool import (
    build_session,
    export_authorized_story,
    search_stories,
    write_csv,
    write_json,
)
from wattpad_export import slugify


BG = "#f4efe7"
SURFACE = "#fffaf2"
SURFACE_ALT = "#f7efe3"
BORDER = "#decebb"
TEXT = "#2f241a"
MUTED = "#7a6b5f"
ACCENT = "#ba613b"
ACCENT_DARK = "#8f4221"
INK = "#183449"
SUCCESS = "#3a6b5b"
WARNING = "#bf7a28"
LOG_BG = "#10202d"
LOG_FG = "#e7eef7"


def ui_font_family() -> str:
    if sys.platform == "darwin":
        return "PingFang SC"
    if os.name == "nt":
        return "Microsoft YaHei UI"
    return "Noto Sans CJK SC"


def mono_font_family() -> str:
    if sys.platform == "darwin":
        return "SF Mono"
    if os.name == "nt":
        return "Consolas"
    return "DejaVu Sans Mono"


def default_output_root() -> Path:
    downloads = default_downloads_dir()
    base = downloads if downloads.exists() else Path.home()
    return base / "WattpadTool"


def default_downloads_dir() -> Path:
    downloads = Path.home() / "Downloads"
    return downloads if downloads.exists() else Path.home()


def normalize_zip_path(raw_path: str | Path) -> Path:
    path = Path(raw_path).expanduser()
    if path.suffix.lower() != ".zip":
        path = path.with_name(path.name + ".zip")
    return path.resolve()


def format_number(value: int | None) -> str:
    return f"{int(value or 0):,}"


def type_text(story: dict) -> str:
    return "付费" if story.get("isPaywalled") else "免费"


def status_text(story: dict) -> str:
    return "已完结" if story.get("completed") else "连载中"


def maturity_text(story: dict) -> str:
    return "成熟内容" if story.get("mature") else "普通"


def shorten(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def open_target(target: str | Path) -> None:
    if isinstance(target, Path):
        path_str = str(target)
    else:
        path_str = str(target)

    if path_str.startswith("http://") or path_str.startswith("https://"):
        webbrowser.open(path_str)
        return

    if sys.platform == "darwin":
        subprocess.run(["open", path_str], check=False)
        return
    if os.name == "nt":
        os.startfile(path_str)  # type: ignore[attr-defined]
        return
    subprocess.run(["xdg-open", path_str], check=False)


def reveal_target(target: str | Path) -> None:
    path = Path(target).expanduser().resolve()
    if sys.platform == "darwin":
        if path.exists() and path.is_file():
            subprocess.run(["open", "-R", str(path)], check=False)
            return
        subprocess.run(["open", str(path)], check=False)
        return
    if os.name == "nt":
        if path.exists() and path.is_file():
            subprocess.run(["explorer", f"/select,{path}"], check=False)
            return
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    open_target(path.parent if path.is_file() else path)


def localize_log_line(line: str) -> str:
    if not line:
        return line

    patterns = [
        (
            re.compile(r"^\[(\d+)/(\d+)\] Fetching (.+) \((\d+)\)\.\.\.$"),
            lambda m: f"[{m.group(1)}/{m.group(2)}] 正在抓取章节 {m.group(3)}（{m.group(4)}）...",
        ),
        (
            re.compile(r"^Translating (\d+) blocks\.\.\.$"),
            lambda m: f"正在翻译 {format_number(int(m.group(1)))} 个文本块...",
        ),
        (
            re.compile(r"^Updated (\d+)/(\d+) translated blocks\.\.\.$"),
            lambda m: f"翻译进度：已完成 {format_number(int(m.group(1)))}/{format_number(int(m.group(2)))} 个文本块...",
        ),
    ]

    direct_map = {
        "JSON: ": "JSON 文件：",
        "CSV: ": "CSV 文件：",
        "Metadata: ": "元数据：",
        "English HTML: ": "英文 HTML：",
        "English DOCX: ": "英文 DOCX：",
        "Chinese HTML: ": "中文 HTML：",
        "Chinese DOCX: ": "中文 DOCX：",
        "ZIP: ": "ZIP 压缩包：",
        "metadata_json: ": "元数据：",
        "english_html: ": "英文 HTML：",
        "english_docx: ": "英文 DOCX：",
        "chinese_html: ": "中文 HTML：",
        "chinese_docx: ": "中文 DOCX：",
    }

    for pattern, formatter in patterns:
        match = pattern.match(line)
        if match:
            return formatter(match)

    for prefix, translated_prefix in direct_map.items():
        if line.startswith(prefix):
            return translated_prefix + line[len(prefix) :]

    return line


class QueueWriter(io.TextIOBase):
    def __init__(self, sink_queue: queue.Queue):
        self.sink_queue = sink_queue
        self._buffer = ""

    def write(self, data: str) -> int:
        if not data:
            return 0
        self._buffer += data
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self.sink_queue.put(("log", line))
        return len(data)

    def flush(self) -> None:
        if self._buffer:
            self.sink_queue.put(("log", self._buffer))
            self._buffer = ""


class WattpadApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Wattpad")
        self.root.geometry("1000x700")
        self.root.minsize(720, 480)
        self.root.configure(bg=BG)

        self.events: queue.Queue = queue.Queue()
        self.worker_running = False
        self.last_output_target: Path | None = None
        self.current_search_payload: dict | None = None

        self.status_var = tk.StringVar(value="")

        self.search_keyword = tk.StringVar()
        self.search_max_results = tk.IntVar(value=20)
        self.search_page_size = tk.IntVar(value=50)
        self.search_include_mature = tk.BooleanVar(value=False)
        self.search_include_paywalled = tk.BooleanVar(value=False)
        self.search_save_json = tk.BooleanVar(value=False)
        self.search_save_csv = tk.BooleanVar(value=False)
        self.search_output_dir = tk.StringVar(value=str(default_output_root() / "search"))

        self.export_translate = tk.BooleanVar(value=False)
        self.export_cookies_path = tk.StringVar()

        self._build_ui()
        self.root.after(120, self._poll_events)

    def _build_ui(self) -> None:
        self._build_styles()

        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill="both", expand=True, padx=16, pady=16)

        self._build_header(shell)

        body = tk.Frame(shell, bg=BG)
        body.pack(fill="both", expand=True, pady=(14, 0))

        main_pane = ttk.Panedwindow(body, orient="vertical", style="App.TPanedwindow")
        main_pane.pack(fill="both", expand=True)

        top = tk.Frame(main_pane, bg=BG)
        bottom = tk.Frame(main_pane, bg=BG)
        main_pane.add(top, weight=5)
        main_pane.add(bottom, weight=2)

        work = ttk.Frame(top, style="App.TFrame", padding=12)
        work.pack(fill="both", expand=True)
        self._build_main_panel(work)
        self._build_log_area(bottom)

    def _build_styles(self) -> None:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        font_family = ui_font_family()

        style.configure(".", font=(font_family, 11), background=BG, foreground=TEXT)
        style.configure("App.TFrame", background=BG)
        style.configure("Card.TFrame", background=SURFACE)
        style.configure("Surface.TLabelframe", background=SURFACE, borderwidth=1, relief="solid", bordercolor=BORDER)
        style.configure("Surface.TLabelframe.Label", background=SURFACE, foreground=TEXT, font=(font_family, 12, "bold"))
        style.configure("HeroTitle.TLabel", background=INK, foreground="#ffffff", font=(font_family, 26, "bold"))
        style.configure("HeroSub.TLabel", background=INK, foreground="#d8e4ee", font=(font_family, 11))
        style.configure("CardTitle.TLabel", background=SURFACE, foreground=TEXT, font=(font_family, 13, "bold"))
        style.configure("Muted.TLabel", background=SURFACE, foreground=MUTED, font=(font_family, 10))
        style.configure("Body.TLabel", background=SURFACE, foreground=TEXT, font=(font_family, 11))
        style.configure("Section.TLabel", background=BG, foreground=TEXT, font=(font_family, 12, "bold"))
        style.configure("Accent.TButton", font=(font_family, 11, "bold"), padding=(16, 10), background=ACCENT, foreground="#ffffff", borderwidth=0)
        style.map("Accent.TButton", background=[("active", ACCENT_DARK), ("disabled", "#d6b6a4")], foreground=[("disabled", "#fff6ef")])
        style.configure("Soft.TButton", padding=(12, 8), background=SURFACE_ALT, foreground=TEXT, borderwidth=0)
        style.map("Soft.TButton", background=[("active", "#efe4d6")])
        style.configure("App.TNotebook", background=BG, borderwidth=0)
        style.configure("App.TNotebook.Tab", padding=(18, 10), font=(font_family, 11, "bold"), background="#eadfce", foreground=MUTED)
        style.map("App.TNotebook.Tab", background=[("selected", SURFACE), ("active", "#efe3d5")], foreground=[("selected", TEXT), ("active", TEXT)])
        style.configure("App.Treeview", font=(font_family, 10), rowheight=32, background="#fffefb", fieldbackground="#fffefb", foreground=TEXT, borderwidth=0)
        style.configure("App.Treeview.Heading", font=(font_family, 10, "bold"), background="#eadfce", foreground=TEXT, relief="flat")
        style.map("App.Treeview", background=[("selected", "#e9d4c8")], foreground=[("selected", TEXT)])
        style.configure("App.Vertical.TScrollbar", background="#d6c6b7", troughcolor=SURFACE_ALT, bordercolor=SURFACE_ALT, arrowcolor=TEXT)
        style.configure("App.Horizontal.TScrollbar", background="#d6c6b7", troughcolor=SURFACE_ALT, bordercolor=SURFACE_ALT, arrowcolor=TEXT)
        style.configure("Status.TLabel", background=BG, foreground=MUTED, font=(font_family, 10))
        style.configure("App.TPanedwindow", background=BG)

    def _build_header(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, bg=INK, highlightthickness=0, padx=16, pady=12)
        header.pack(fill="x")
        ttk.Label(header, text="Wattpad", style="HeroTitle.TLabel").pack(anchor="w")

    def _build_log_area(self, parent: tk.Frame) -> None:
        status_row = tk.Frame(parent, bg=BG)
        status_row.pack(fill="x", pady=(0, 6))

        status_card = tk.Frame(status_row, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, padx=10, pady=6)
        status_card.pack(side="left", fill="x", expand=True)
        tk.Label(status_card, textvariable=self.status_var, bg=SURFACE, fg=TEXT, font=(ui_font_family(), 11)).pack(side="left", anchor="w")

        ttk.Button(status_row, text="清空", style="Soft.TButton", command=self._clear_log).pack(side="right")
        ttk.Button(status_row, text="输出", style="Soft.TButton", command=self._open_last_output).pack(side="right", padx=(0, 8))

        log_card = tk.Frame(parent, bg=LOG_BG, highlightbackground="#203142", highlightthickness=1)
        log_card.pack(fill="both", expand=True)

        log_header = tk.Frame(log_card, bg=LOG_BG, padx=10, pady=6)
        log_header.pack(fill="x")
        tk.Label(log_header, text="日志", bg=LOG_BG, fg="#ffffff", font=(ui_font_family(), 11, "bold")).pack(side="left")

        log_body = tk.Frame(log_card, bg=LOG_BG)
        log_body.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.log_text = tk.Text(
            log_body,
            wrap="word",
            height=8,
            bg=LOG_BG,
            fg=LOG_FG,
            relief="flat",
            insertbackground=LOG_FG,
            selectbackground="#355f88",
            font=(mono_font_family(), 10),
            padx=12,
            pady=10,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.configure(state="disabled")
        self.log_text.tag_configure("normal", foreground=LOG_FG)
        self.log_text.tag_configure("success", foreground="#8fe1b8")
        self.log_text.tag_configure("progress", foreground="#f5c779")
        self.log_text.tag_configure("error", foreground="#ff9f9f")
        self.log_text.tag_configure("muted", foreground="#9ab1c5")

        scroll = ttk.Scrollbar(log_body, orient="vertical", style="App.Vertical.TScrollbar", command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)

    def _build_main_panel(self, parent: ttk.Frame) -> None:
        ctl = tk.Frame(parent, bg=BG)
        ctl.pack(fill="x", pady=(0, 8))

        ttk.Label(ctl, text="关键词", style="Body.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(ctl, textvariable=self.search_keyword).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.search_button = ttk.Button(ctl, text="搜索", style="Accent.TButton", command=self._start_search)
        self.search_button.grid(row=0, column=2, sticky="e")

        ttk.Label(ctl, text="最多", style="Body.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(ctl, from_=1, to=200, textvariable=self.search_max_results, width=6).grid(row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Label(ctl, text="每页", style="Body.TLabel").grid(row=1, column=2, sticky="w", padx=(12, 0), pady=(8, 0))
        ttk.Spinbox(ctl, from_=5, to=100, textvariable=self.search_page_size, width=6).grid(row=1, column=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(ctl, text="成熟", variable=self.search_include_mature).grid(row=1, column=4, sticky="w", padx=(12, 0), pady=(8, 0))
        ttk.Checkbutton(ctl, text="付费", variable=self.search_include_paywalled).grid(row=1, column=5, sticky="w", pady=(8, 0))

        ttk.Checkbutton(ctl, text="JSON", variable=self.search_save_json).grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Checkbutton(ctl, text="CSV", variable=self.search_save_csv).grid(row=2, column=1, sticky="w", pady=(6, 0))

        ttk.Label(ctl, text="目录", style="Body.TLabel").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(ctl, textvariable=self.search_output_dir).grid(row=3, column=1, columnspan=5, sticky="ew", padx=(0, 6), pady=(8, 0))
        ttk.Button(ctl, text="…", style="Soft.TButton", width=3, command=lambda: self._choose_directory(self.search_output_dir)).grid(
            row=3, column=6, sticky="e", pady=(8, 0)
        )

        ctl.columnconfigure(1, weight=1)

        split = ttk.Panedwindow(parent, orient="horizontal", style="App.TPanedwindow")
        split.pack(fill="both", expand=True)

        left_wrap = tk.Frame(split, bg=BG)
        card = tk.Frame(left_wrap, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="both", expand=True)

        body = tk.Frame(card, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        columns = ("rank", "title", "author", "reads", "votes", "parts", "type")
        self.results_tree = ttk.Treeview(
            body,
            columns=columns,
            show="headings",
            style="App.Treeview",
            selectmode="extended",
        )
        headings = {
            "rank": "#",
            "title": "标题",
            "author": "作者",
            "reads": "阅读",
            "votes": "票",
            "parts": "章",
            "type": "类",
        }
        widths = {"rank": 44, "title": 280, "author": 120, "reads": 88, "votes": 72, "parts": 56, "type": 56}
        anchors = {
            "rank": "center",
            "title": "w",
            "author": "w",
            "reads": "e",
            "votes": "e",
            "parts": "center",
            "type": "center",
        }
        for col in columns:
            self.results_tree.heading(col, text=headings[col])
            self.results_tree.column(col, width=widths[col], anchor=anchors[col], stretch=col == "title")

        self.results_tree.bind("<<TreeviewSelect>>", self._on_result_select)
        self.results_tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(body, orient="vertical", style="App.Vertical.TScrollbar", command=self.results_tree.yview)
        scroll.pack(side="right", fill="y")
        self.results_tree.configure(yscrollcommand=scroll.set)

        actions = tk.Frame(card, bg=SURFACE)
        actions.pack(fill="x", padx=8, pady=(0, 8))
        self.export_selected_button = ttk.Button(
            actions,
            text="导出",
            style="Accent.TButton",
            command=self._start_batch_export_from_search,
        )
        self.export_selected_button.pack(side="left", fill="x", expand=True)
        ttk.Button(actions, text="复制", style="Soft.TButton", command=self._copy_selected_story_url).pack(side="right", padx=(8, 0))
        ttk.Button(actions, text="全选", style="Soft.TButton", command=self._select_all_search_results).pack(side="right")

        right_wrap = tk.Frame(split, bg=BG)
        preview = tk.Frame(right_wrap, bg=SURFACE_ALT, highlightbackground=BORDER, highlightthickness=1)
        preview.pack(fill="both", expand=True)
        tk.Label(preview, text="预览", bg=SURFACE_ALT, fg=TEXT, font=(ui_font_family(), 12, "bold")).pack(anchor="w", padx=10, pady=(8, 4))
        detail_body = tk.Frame(preview, bg=SURFACE_ALT)
        detail_body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.detail_text = tk.Text(
            detail_body,
            wrap="word",
            bg=SURFACE_ALT,
            fg=TEXT,
            relief="flat",
            font=(ui_font_family(), 10),
            padx=4,
            pady=4,
            spacing1=2,
            spacing2=2,
            spacing3=6,
        )
        self.detail_text.pack(side="left", fill="both", expand=True)
        self.detail_text.configure(state="disabled")
        self.detail_text.tag_configure("title", font=(ui_font_family(), 13, "bold"), foreground=TEXT)
        self.detail_text.tag_configure("label", font=(ui_font_family(), 9, "bold"), foreground=ACCENT_DARK)
        self.detail_text.tag_configure("muted", foreground=MUTED)
        dscroll = ttk.Scrollbar(detail_body, orient="vertical", style="App.Vertical.TScrollbar", command=self.detail_text.yview)
        dscroll.pack(side="right", fill="y")
        self.detail_text.configure(yscrollcommand=dscroll.set)

        split.add(left_wrap, weight=3)
        split.add(right_wrap, weight=2)
        self._render_story_detail(None)

    def _render_story_detail(self, story: dict | None) -> None:
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")

        if not story:
            self.detail_text.insert("end", "—", "muted")
            self.detail_text.configure(state="disabled")
            return

        self.detail_text.insert("end", story.get("title", "") + "\n", "title")
        self.detail_text.insert("end", f"作者：{story.get('author', '')}\n\n", "muted")

        fields = [
            ("阅读", format_number(story.get("readCount"))),
            ("投票", format_number(story.get("voteCount"))),
            ("评论", format_number(story.get("commentCount"))),
            ("章节", format_number(story.get("numParts"))),
            ("状态", status_text(story)),
            ("类型", type_text(story)),
            ("级别", maturity_text(story)),
            ("更新", str(story.get("lastPublishedPart") or "—")),
            ("链接", story.get("url") or ""),
            ("标签", "、".join(story.get("tags", [])) or "—"),
        ]
        for label, value in fields:
            self.detail_text.insert("end", label + "：", "label")
            self.detail_text.insert("end", value + "\n")

        self.detail_text.insert("end", "\n简介\n", "label")
        self.detail_text.insert("end", story.get("description") or "—")
        self.detail_text.configure(state="disabled")

    def _on_result_select(self, _event=None) -> None:
        if not self.current_search_payload:
            return
        selected = self.results_tree.selection()
        if not selected:
            self._render_story_detail(None)
            return
        stories = self.current_search_payload.get("stories", [])
        first = min(int(i) for i in selected)
        if 0 <= first < len(stories):
            self._render_story_detail(stories[first])

    def _choose_directory(self, variable: tk.StringVar) -> None:
        chosen = filedialog.askdirectory(initialdir=variable.get() or str(default_output_root()))
        if chosen:
            variable.set(chosen)

    def _choose_export_cookies_file(self) -> None:
        initial = self.export_cookies_path.get().strip()
        initial_dir = str(Path(initial).expanduser().parent) if initial else str(Path.home())
        chosen = filedialog.askopenfilename(
            title="",
            initialdir=initial_dir,
            filetypes=[
                ("Cookie / JSON", "*.txt *.json"),
                ("所有文件", "*.*"),
            ],
        )
        if chosen:
            self.export_cookies_path.set(chosen)

    def _open_last_output(self) -> None:
        if not self.last_output_target or not self.last_output_target.exists():
            return
        reveal_target(self.last_output_target)

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _append_log(self, line: str) -> None:
        localized = localize_log_line(line)
        tag = "normal"
        if localized.startswith("翻译进度") or "正在抓取" in localized or "正在翻译" in localized:
            tag = "progress"
        elif "完成" in localized or localized.startswith(("JSON 文件", "CSV 文件", "元数据", "英文 ", "中文 ", "ZIP 压缩包")):
            tag = "success"
        elif "Traceback" in localized or "Error" in localized or "错误" in localized or "Refusing" in localized:
            tag = "error"
        elif localized.startswith("关键词：") or localized.startswith("匹配总数："):
            tag = "muted"

        self.log_text.configure(state="normal")
        self.log_text.insert("end", localized + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self.worker_running = busy
        state = "disabled" if busy else "normal"
        self.search_button.configure(state=state)
        self.export_selected_button.configure(state=state)

    def _run_in_worker(self, label: str, fn) -> None:
        if self.worker_running:
            return

        self._set_busy(True)
        self.status_var.set(label)

        def target() -> None:
            writer = QueueWriter(self.events)
            try:
                with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                    fn()
            except Exception:  # noqa: BLE001
                writer.flush()
                self.events.put(("error", traceback.format_exc()))
            else:
                writer.flush()
                self.events.put(("done", None))

        threading.Thread(target=target, daemon=True).start()

    def _populate_search_results(self, payload: dict) -> None:
        self.current_search_payload = payload
        stories = payload.get("stories", [])
        total = int(payload.get("total") or 0)
        self.status_var.set(f"{len(stories)}/{total}")

        self.results_tree.delete(*self.results_tree.get_children())
        for idx, story in enumerate(stories, start=1):
            self.results_tree.insert(
                "",
                "end",
                iid=str(idx - 1),
                values=(
                    idx,
                    shorten(story["title"], 42),
                    shorten(story["author"], 18),
                    format_number(story["readCount"]),
                    format_number(story["voteCount"]),
                    format_number(story["numParts"]),
                    type_text(story),
                ),
            )

        if stories:
            self.results_tree.selection_set("0")
            self.results_tree.focus("0")
            self._render_story_detail(stories[0])
        else:
            self._render_story_detail(None)

    def _select_all_search_results(self) -> None:
        if not self.current_search_payload:
            return
        stories = self.current_search_payload.get("stories", [])
        if not stories:
            return
        ids = tuple(str(i) for i in range(len(stories)))
        self.results_tree.selection_set(ids)
        self.results_tree.focus(ids[-1])
        self._render_story_detail(stories[0])

    def _get_selected_stories_ordered(self) -> list[dict]:
        if not self.current_search_payload:
            return []
        selected = self.results_tree.selection()
        if not selected:
            return []
        stories = self.current_search_payload.get("stories", [])
        indices = sorted(int(i) for i in selected)
        return [stories[i] for i in indices if 0 <= i < len(stories)]

    def _copy_selected_story_url(self) -> None:
        chosen = self._get_selected_stories_ordered()
        if not chosen:
            return
        urls = "\n".join(s.get("url", "") for s in chosen if s.get("url"))
        self.root.clipboard_clear()
        self.root.clipboard_append(urls)

    def _ask_export_archive_path(self, suggested_basename: str) -> Path | None:
        initial_target = self.last_output_target or default_downloads_dir()
        initial_dir = initial_target if initial_target.is_dir() else initial_target.parent
        chosen = filedialog.asksaveasfilename(
            title="",
            initialdir=str(initial_dir),
            initialfile=f"{suggested_basename}.zip",
            defaultextension=".zip",
            filetypes=[("ZIP", "*.zip")],
        )
        if not chosen:
            return None
        return normalize_zip_path(chosen)

    def _show_batch_export_confirm_dialog(self, stories: list[dict]) -> tuple[bool, Path | None] | None:
        """Returns (translate_to_chinese, cookies_path) or None if cancelled."""
        any_paywalled = any(bool(s.get("isPaywalled")) for s in stories)
        outcome: list[tuple[bool, Path | None] | None] = [None]

        dlg = tk.Toplevel(self.root)
        dlg.title("导出")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg=SURFACE)
        dlg.minsize(380, 260)

        shell = tk.Frame(dlg, bg=SURFACE, padx=12, pady=10)
        shell.pack(fill="both", expand=True)

        tk.Label(shell, text=str(len(stories)), bg=SURFACE, fg=TEXT, font=(ui_font_family(), 14, "bold")).pack(anchor="w")

        list_wrap = tk.Frame(shell, bg=SURFACE)
        list_wrap.pack(fill="both", expand=True, pady=(4, 8))
        scroll = ttk.Scrollbar(list_wrap, style="App.Vertical.TScrollbar")
        scroll.pack(side="right", fill="y")
        lines = min(12, max(4, len(stories)))
        body_txt = tk.Text(
            list_wrap,
            height=lines,
            width=56,
            wrap="word",
            font=(ui_font_family(), 10),
            bg="#fffefb",
            fg=TEXT,
            relief="flat",
            yscrollcommand=scroll.set,
        )
        body_txt.pack(side="left", fill="both", expand=True)
        scroll.config(command=body_txt.yview)
        for i, s in enumerate(stories, start=1):
            body_txt.insert("end", f"{i}. {s.get('title', '')}\n")
        body_txt.configure(state="disabled")

        translate_var = tk.BooleanVar(value=self.export_translate.get())
        ttk.Checkbutton(shell, text="中文", variable=translate_var).pack(anchor="w", pady=(0, 4))

        if any_paywalled:
            row = tk.Frame(shell, bg=SURFACE)
            row.pack(fill="x", pady=(0, 6))
            ttk.Entry(row, textvariable=self.export_cookies_path).pack(side="left", fill="x", expand=True, padx=(0, 6))
            ttk.Button(row, text="…", style="Soft.TButton", width=3, command=self._choose_export_cookies_file).pack(side="right")

        btn_row = tk.Frame(shell, bg=SURFACE)
        btn_row.pack(fill="x", pady=(8, 0))

        def finish(cancelled: bool) -> None:
            if cancelled:
                outcome[0] = None
                dlg.destroy()
                return
            raw = self.export_cookies_path.get().strip()
            cpath = Path(raw).expanduser().resolve() if raw else None
            if any_paywalled and (not cpath or not cpath.is_file()):
                messagebox.showerror("Cookie", "Cookie", parent=dlg)
                return
            self.export_translate.set(bool(translate_var.get()))
            outcome[0] = (bool(translate_var.get()), cpath)
            dlg.destroy()

        ttk.Button(btn_row, text="取消", style="Soft.TButton", command=lambda: finish(True)).pack(side="right", padx=(6, 0))
        ttk.Button(btn_row, text="确定", style="Accent.TButton", command=lambda: finish(False)).pack(side="right")

        dlg.protocol("WM_DELETE_WINDOW", lambda: finish(True))

        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_reqheight()) // 2
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")

        self.root.wait_window(dlg)
        return outcome[0]

    def _start_batch_export_from_search(self) -> None:
        stories = self._get_selected_stories_ordered()
        if not stories:
            return

        settings = self._show_batch_export_confirm_dialog(stories)
        if settings is None:
            return

        translate, cookies_path = settings
        keyword = self.search_keyword.get().strip()
        suggest = f"{slugify(keyword) or 'batch'}-{len(stories)}部"
        archive_path = self._ask_export_archive_path(suggest)
        if archive_path is None:
            return

        def worker() -> None:
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="wattpad-batch-") as temp_root:
                staging = Path(temp_root) / archive_path.stem.strip()
                staging.mkdir(parents=True, exist_ok=True)
                staging = staging.resolve()
                session = build_session()
                all_docx: list[Path] = []
                try:
                    for idx, story in enumerate(stories, start=1):
                        print(f"[批量 {idx}/{len(stories)}] {story.get('title', '')}")
                        url = story.get("url") or ""
                        if not url:
                            raise RuntimeError(f"缺少作品链接：{story.get('title', '')!r}")
                        sid = story.get("id") or idx
                        folder_name = f"{slugify(story.get('title', '') or 'story')}-{sid}".strip()
                        per_dir = (staging / folder_name).resolve()
                        per_dir.mkdir(parents=True, exist_ok=True)
                        result = export_authorized_story(
                            session=session,
                            story_url=url,
                            output_dir=per_dir,
                            basename=None,
                            translate_to_chinese=translate,
                            cookies_path=cookies_path,
                        )
                        all_docx.append(Path(result["english_docx"]).resolve())
                        if "chinese_docx" in result:
                            all_docx.append(Path(result["chinese_docx"]).resolve())
                finally:
                    session.close()

                zip_root = staging.resolve()
                if archive_path.exists():
                    archive_path.unlink()
                with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for docx in all_docx:
                        arcname = docx.resolve().relative_to(zip_root).as_posix()
                        archive.write(docx, arcname)

            print(f"ZIP: {archive_path}")
            self.events.put(("output-target", archive_path))
            self.events.put(("status", archive_path.name))

        self._run_in_worker("", worker)

    def _start_search(self) -> None:
        keyword = self.search_keyword.get().strip()
        if not keyword:
            return

        output_dir = Path(self.search_output_dir.get().strip() or default_output_root() / "search").expanduser().resolve()
        save_json = self.search_save_json.get()
        save_csv = self.search_save_csv.get()
        max_results = self.search_max_results.get()
        page_size = self.search_page_size.get()
        include_mature = self.search_include_mature.get()
        include_paywalled = self.search_include_paywalled.get()

        def worker() -> None:
            session = build_session()
            try:
                payload = search_stories(
                    session=session,
                    keyword=keyword,
                    max_results=max_results,
                    page_size=page_size,
                    include_mature=include_mature,
                    include_paywalled=include_paywalled,
                )
            finally:
                session.close()

            base = slugify(keyword) or "search"
            saved_paths = []
            if save_json or save_csv:
                output_dir.mkdir(parents=True, exist_ok=True)
            if save_json:
                json_path = output_dir / f"{base}-results.json"
                write_json(json_path, payload)
                saved_paths.append(json_path)
                print(f"JSON: {json_path}")
            if save_csv:
                csv_path = output_dir / f"{base}-results.csv"
                write_csv(csv_path, payload["stories"])
                saved_paths.append(csv_path)
                print(f"CSV: {csv_path}")

            print(f"关键词：{keyword}")
            print(f"匹配总数：{format_number(payload.get('total', 0))}")
            print(f"当前返回：{format_number(payload.get('returned', 0))}")
            self.events.put(("search-payload", payload))
            if saved_paths:
                self.events.put(("output-target", output_dir))

        self._run_in_worker("", worker)

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    if payload:
                        self._append_log(payload)
                elif kind == "search-payload":
                    self._populate_search_results(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "output-target":
                    self.last_output_target = Path(payload)
                elif kind == "done":
                    self._set_busy(False)
                elif kind == "error":
                    self._set_busy(False)
                    self.status_var.set("")
                    self._append_log(payload)
                    messagebox.showerror("Err", payload[:800])
        except queue.Empty:
            pass
        finally:
            self.root.after(120, self._poll_events)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-test", action="store_true")
    args, _ = parser.parse_known_args()

    if args.self_test:
        print("OK")
        print(f"Python: {sys.version.split()[0]}")
        print(f"Platform: {sys.platform}")
        return 0

    root = tk.Tk()
    WattpadApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
