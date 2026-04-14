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


def status_text(story: dict) -> str:
    return "已完结" if story.get("completed") else "连载中"


def type_text(story: dict) -> str:
    return "付费" if story.get("isPaywalled") else "免费"


def maturity_text(story: dict) -> str:
    return "成熟内容" if story.get("mature") else "普通"


def shorten(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def format_search_results(payload: dict) -> str:
    stories = payload.get("stories", [])
    lines = [
        f"关键词：{payload.get('keyword', '')}",
        f"匹配总数：{format_number(payload.get('total', 0))}",
        f"当前返回：{format_number(payload.get('returned', len(stories)))}",
        "排序方式：阅读量 > 投票数 > 评论数 > 章节数",
        "",
    ]
    for idx, story in enumerate(stories, start=1):
        lines.append(
            f"{idx}. {story['title']} | 作者：{story['author']} | 阅读：{format_number(story['readCount'])} | "
            f"投票：{format_number(story['voteCount'])} | 评论：{format_number(story['commentCount'])} | "
            f"章节：{format_number(story['numParts'])} | 类型：{type_text(story)}"
        )
        lines.append(f"   {story['url']}")
    return "\n".join(lines)


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


def bundle_export_files(paths: list[Path], archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        archive_path.unlink()

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in paths:
            archive.write(file_path, file_path.name)


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
        self.root.title("Wattpad 中文工具箱")
        self.root.geometry("1240x840")
        self.root.minsize(1080, 740)
        self.root.configure(bg=BG)

        self.events: queue.Queue = queue.Queue()
        self.worker_running = False
        self.last_output_target: Path | None = None
        self.current_search_payload: dict | None = None
        self.current_selected_story: dict | None = None

        self.status_var = tk.StringVar(value="就绪")
        self.search_total_var = tk.StringVar(value="0")
        self.search_returned_var = tk.StringVar(value="0")
        self.search_sort_var = tk.StringVar(value="阅读量 > 投票数 > 评论数")

        self.search_keyword = tk.StringVar()
        self.search_max_results = tk.IntVar(value=20)
        self.search_page_size = tk.IntVar(value=50)
        self.search_include_mature = tk.BooleanVar(value=False)
        self.search_include_paywalled = tk.BooleanVar(value=False)
        self.search_save_json = tk.BooleanVar(value=False)
        self.search_save_csv = tk.BooleanVar(value=False)
        self.search_output_dir = tk.StringVar(value=str(default_output_root() / "search"))

        self.export_story_url = tk.StringVar()
        self.export_authorized = tk.BooleanVar(value=False)
        self.export_translate = tk.BooleanVar(value=True)
        self.export_basename = tk.StringVar()
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

        self.notebook = ttk.Notebook(top, style="App.TNotebook")
        self.notebook.pack(fill="both", expand=True)

        self.search_tab = ttk.Frame(self.notebook, style="App.TFrame", padding=16)
        self.export_tab = ttk.Frame(self.notebook, style="App.TFrame", padding=16)
        self.notebook.add(self.search_tab, text="关键词搜索")
        self.notebook.add(self.export_tab, text="授权导出")

        self._build_search_tab(self.search_tab)
        self._build_export_tab(self.export_tab)
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
        header = tk.Frame(parent, bg=INK, highlightthickness=0, padx=26, pady=22)
        header.pack(fill="x")

        left = tk.Frame(header, bg=INK)
        left.pack(side="left", fill="x", expand=True)

        ttk.Label(left, text="Wattpad 中文工具箱", style="HeroTitle.TLabel").pack(anchor="w")
        ttk.Label(
            left,
            text="搜索公开作品元数据，按热度排序；对你自己的作品或已获授权作品，导出英文版和中文版文档。作者本人的付费作品在提供浏览器 Cookie 后也可导出。",
            style="HeroSub.TLabel",
        ).pack(anchor="w", pady=(6, 0))

        badges = tk.Frame(header, bg=INK)
        badges.pack(side="right", anchor="ne")
        for label, color in [("全中文界面", ACCENT), ("搜索排序", SUCCESS), ("双语导出", WARNING)]:
            pill = tk.Label(
                badges,
                text=label,
                bg=color,
                fg="#ffffff",
                padx=12,
                pady=6,
                font=(ui_font_family(), 10, "bold"),
            )
            pill.pack(side="left", padx=(8, 0))

    def _build_log_area(self, parent: tk.Frame) -> None:
        status_row = tk.Frame(parent, bg=BG)
        status_row.pack(fill="x", pady=(0, 8))

        status_card = tk.Frame(status_row, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, padx=12, pady=10)
        status_card.pack(side="left", fill="x", expand=True)
        tk.Label(status_card, text="运行状态", bg=SURFACE, fg=MUTED, font=(ui_font_family(), 10)).pack(side="left")
        tk.Label(status_card, textvariable=self.status_var, bg=SURFACE, fg=TEXT, font=(ui_font_family(), 11, "bold")).pack(side="left", padx=(10, 0))

        ttk.Button(status_row, text="清空日志", style="Soft.TButton", command=self._clear_log).pack(side="right")
        ttk.Button(status_row, text="打开输出位置", style="Accent.TButton", command=self._open_last_output).pack(side="right", padx=(0, 10))

        log_card = tk.Frame(parent, bg=LOG_BG, highlightbackground="#203142", highlightthickness=1)
        log_card.pack(fill="both", expand=True)

        log_header = tk.Frame(log_card, bg=LOG_BG, padx=14, pady=12)
        log_header.pack(fill="x")
        tk.Label(log_header, text="运行日志", bg=LOG_BG, fg="#ffffff", font=(ui_font_family(), 12, "bold")).pack(side="left")
        tk.Label(log_header, text="抓取、翻译与导出进度会实时显示在这里", bg=LOG_BG, fg="#aac0d4", font=(ui_font_family(), 10)).pack(side="left", padx=(10, 0))

        log_body = tk.Frame(log_card, bg=LOG_BG)
        log_body.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.log_text = tk.Text(
            log_body,
            wrap="word",
            height=14,
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

    def _build_search_tab(self, parent: ttk.Frame) -> None:
        intro = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, padx=16, pady=14)
        intro.pack(fill="x")
        tk.Label(intro, text="公开元数据搜索", bg=SURFACE, fg=TEXT, font=(ui_font_family(), 15, "bold")).pack(anchor="w")
        tk.Label(
            intro,
            text="根据关键词检索 Wattpad 公开作品信息，并按阅读量、投票数和评论数综合排序。搜索结果只包含元数据，不会自动整本导出；默认也不会额外保存 json/csv 文件。",
            bg=SURFACE,
            fg=MUTED,
            justify="left",
            wraplength=980,
            font=(ui_font_family(), 10),
        ).pack(anchor="w", pady=(6, 0))

        controls = ttk.LabelFrame(parent, text="检索条件", style="Surface.TLabelframe", padding=14)
        controls.pack(fill="x", pady=(12, 0))

        ttk.Label(controls, text="关键词", style="Body.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.search_keyword, width=42).grid(row=0, column=1, sticky="ew", padx=(8, 16))
        ttk.Label(controls, text="最多返回", style="Body.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(controls, from_=1, to=200, textvariable=self.search_max_results, width=8).grid(row=0, column=3, sticky="w", padx=(8, 0))

        ttk.Label(controls, text="每页抓取量", style="Body.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Spinbox(controls, from_=5, to=100, textvariable=self.search_page_size, width=8).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        ttk.Checkbutton(controls, text="包含成熟内容", variable=self.search_include_mature).grid(row=1, column=2, sticky="w", pady=(10, 0))
        ttk.Checkbutton(controls, text="包含付费作品", variable=self.search_include_paywalled).grid(row=1, column=3, sticky="w", padx=(8, 0), pady=(10, 0))

        ttk.Label(controls, text="结果保存目录", style="Body.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(controls, textvariable=self.search_output_dir).grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 8), pady=(10, 0))
        ttk.Button(controls, text="选择目录", style="Soft.TButton", command=lambda: self._choose_directory(self.search_output_dir)).grid(row=2, column=3, sticky="w", pady=(10, 0))

        ttk.Checkbutton(controls, text="保存 JSON（可选）", variable=self.search_save_json).grid(row=3, column=0, sticky="w", pady=(12, 0))
        ttk.Checkbutton(controls, text="保存 CSV（可选）", variable=self.search_save_csv).grid(row=3, column=1, sticky="w", pady=(12, 0))
        self.search_button = ttk.Button(controls, text="开始搜索", style="Accent.TButton", command=self._start_search)
        self.search_button.grid(row=3, column=3, sticky="e", pady=(12, 0))

        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(2, weight=1)

        stats_row = tk.Frame(parent, bg=BG)
        stats_row.pack(fill="x", pady=(12, 0))
        self._create_stat_card(stats_row, "匹配总数", self.search_total_var).pack(side="left", fill="x", expand=True)
        self._create_stat_card(stats_row, "当前返回", self.search_returned_var).pack(side="left", fill="x", expand=True, padx=12)
        self._create_stat_card(stats_row, "排序规则", self.search_sort_var).pack(side="left", fill="x", expand=True)

        content = ttk.Panedwindow(parent, orient="horizontal", style="App.TPanedwindow")
        content.pack(fill="both", expand=True, pady=(12, 0))

        left = tk.Frame(content, bg=BG)
        right = tk.Frame(content, bg=BG)
        content.add(left, weight=3)
        content.add(right, weight=2)

        self._build_search_results_panel(left)
        self._build_search_detail_panel(right)

    def _build_search_results_panel(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="both", expand=True)

        header = tk.Frame(card, bg=SURFACE, padx=16, pady=14)
        header.pack(fill="x")
        tk.Label(header, text="搜索结果", bg=SURFACE, fg=TEXT, font=(ui_font_family(), 14, "bold")).pack(side="left")
        tk.Label(header, text="选中一条结果后，可直接查看详情或把链接带入导出页。", bg=SURFACE, fg=MUTED, font=(ui_font_family(), 10)).pack(side="left", padx=(12, 0))

        body = tk.Frame(card, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        columns = ("rank", "title", "author", "reads", "votes", "parts", "type")
        self.results_tree = ttk.Treeview(body, columns=columns, show="headings", style="App.Treeview")
        headings = {
            "rank": "序号",
            "title": "作品标题",
            "author": "作者",
            "reads": "阅读量",
            "votes": "投票",
            "parts": "章节",
            "type": "类型",
        }
        widths = {
            "rank": 64,
            "title": 340,
            "author": 140,
            "reads": 120,
            "votes": 100,
            "parts": 80,
            "type": 80,
        }
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
        actions.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Button(actions, text="打开作品页", style="Soft.TButton", command=self._open_selected_story).pack(side="left")
        ttk.Button(actions, text="复制链接", style="Soft.TButton", command=self._copy_selected_story_url).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="带入导出页", style="Accent.TButton", command=self._use_selected_story_url).pack(side="right")

    def _build_search_detail_panel(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=SURFACE_ALT, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="both", expand=True)

        header = tk.Frame(card, bg=SURFACE_ALT, padx=16, pady=14)
        header.pack(fill="x")
        tk.Label(header, text="结果详情", bg=SURFACE_ALT, fg=TEXT, font=(ui_font_family(), 14, "bold")).pack(anchor="w")
        tk.Label(header, text="显示简介、标签、热度和链接。", bg=SURFACE_ALT, fg=MUTED, font=(ui_font_family(), 10)).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(card, bg=SURFACE_ALT)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self.detail_text = tk.Text(
            body,
            wrap="word",
            bg=SURFACE_ALT,
            fg=TEXT,
            relief="flat",
            font=(ui_font_family(), 11),
            padx=6,
            pady=6,
            spacing1=2,
            spacing2=2,
            spacing3=8,
        )
        self.detail_text.pack(side="left", fill="both", expand=True)
        self.detail_text.configure(state="disabled")
        self.detail_text.tag_configure("title", font=(ui_font_family(), 16, "bold"), foreground=TEXT)
        self.detail_text.tag_configure("label", font=(ui_font_family(), 10, "bold"), foreground=ACCENT_DARK)
        self.detail_text.tag_configure("muted", foreground=MUTED)

        scroll = ttk.Scrollbar(body, orient="vertical", style="App.Vertical.TScrollbar", command=self.detail_text.yview)
        scroll.pack(side="right", fill="y")
        self.detail_text.configure(yscrollcommand=scroll.set)
        self._render_story_detail(None)

    def _build_export_tab(self, parent: ttk.Frame) -> None:
        warning_card = tk.Frame(parent, bg="#fff3e6", highlightbackground="#e4c49e", highlightthickness=1, padx=16, pady=14)
        warning_card.pack(fill="x")
        tk.Label(warning_card, text="授权导出", bg="#fff3e6", fg=TEXT, font=(ui_font_family(), 15, "bold")).pack(anchor="w")
        tk.Label(
            warning_card,
            text="这里只接受你明确提供的作品 URL。导出前必须确认作品属于你，或你已经获得明确授权。免费作品可直接导出；若为你的 Wattpad 付费作品（本人作者），请在下方选择登录态 Cookie 文件（与浏览器导出格式一致），工具会校验登录账号与作品作者一致后再导出。导出时会询问压缩包保存位置，默认打开系统下载文件夹。",
            bg="#fff3e6",
            fg=MUTED,
            justify="left",
            wraplength=980,
            font=(ui_font_family(), 10),
        ).pack(anchor="w", pady=(6, 0))

        controls = ttk.LabelFrame(parent, text="导出参数", style="Surface.TLabelframe", padding=14)
        controls.pack(fill="x", pady=(12, 0))

        ttk.Label(controls, text="作品 URL", style="Body.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.export_story_url, width=74).grid(row=0, column=1, columnspan=3, sticky="ew", padx=(8, 0))

        ttk.Label(controls, text="保存方式", style="Body.TLabel").grid(row=1, column=0, sticky="nw", pady=(10, 0))
        ttk.Label(
            controls,
            text=f"点击开始导出后，将弹窗询问 ZIP 压缩包保存位置。\n默认打开：{default_downloads_dir()}",
            style="Muted.TLabel",
            justify="left",
        ).grid(row=1, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(10, 0))

        ttk.Label(controls, text="文件前缀", style="Body.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(controls, textvariable=self.export_basename, width=28).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        ttk.Checkbutton(controls, text="自动生成中文版文档", variable=self.export_translate).grid(row=2, column=2, sticky="w", pady=(10, 0))

        ttk.Label(controls, text="Cookie 文件", style="Body.TLabel").grid(row=3, column=0, sticky="nw", pady=(10, 0))
        cookie_row = tk.Frame(controls, bg=SURFACE)
        cookie_row.grid(row=3, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(10, 0))
        ttk.Entry(cookie_row, textvariable=self.export_cookies_path, width=62).pack(side="left", fill="x", expand=True)
        ttk.Button(cookie_row, text="选择文件…", style="Soft.TButton", command=self._choose_export_cookies_file).pack(side="left", padx=(8, 0))
        ttk.Label(
            controls,
            text="可选。作者导出本人付费作品时：在浏览器登录 Wattpad 后导出 cookies.txt（Netscape）或 JSON，再选择该文件。",
            style="Muted.TLabel",
            justify="left",
            wraplength=720,
        ).grid(row=4, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(4, 0))

        ttk.Checkbutton(
            controls,
            text="我确认：该作品属于我，或我已经获得明确授权",
            variable=self.export_authorized,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(12, 0))

        self.export_button = ttk.Button(controls, text="开始导出", style="Accent.TButton", command=self._start_export)
        self.export_button.grid(row=5, column=3, sticky="e", pady=(12, 0))

        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(2, weight=1)

        flow = tk.Frame(parent, bg=BG)
        flow.pack(fill="both", expand=True, pady=(12, 0))

        left = self._create_info_card(
            flow,
            "将生成的文件",
            [
                "英文 DOCX 文档",
                "中文 DOCX 文档",
                "最终自动打包为一个 ZIP 压缩包",
                "压缩包内默认只保留最终 Word 文档",
            ],
        )
        left.pack(side="left", fill="both", expand=True)

        right = self._create_info_card(
            flow,
            "处理流程",
            [
                "1. 询问 ZIP 保存位置，默认下载文件夹",
                "2. 读取故事目录和章节列表（付费书需 Cookie 且校验作者账号）",
                "3. 抓取各章节正文并生成英文稿",
                "4. 按段翻译为简体中文",
                "5. 只保留最终 DOCX 并自动打包",
            ],
        )
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

    def _create_stat_card(self, parent: tk.Frame, title: str, variable: tk.StringVar) -> tk.Frame:
        card = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, padx=16, pady=14)
        tk.Label(card, text=title, bg=SURFACE, fg=MUTED, font=(ui_font_family(), 10)).pack(anchor="w")
        tk.Label(card, textvariable=variable, bg=SURFACE, fg=TEXT, font=(ui_font_family(), 20, "bold")).pack(anchor="w", pady=(6, 0))
        return card

    def _create_info_card(self, parent: tk.Frame, title: str, lines: list[str]) -> tk.Frame:
        card = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, padx=16, pady=14)
        tk.Label(card, text=title, bg=SURFACE, fg=TEXT, font=(ui_font_family(), 14, "bold")).pack(anchor="w")
        for line in lines:
            tk.Label(card, text=line, bg=SURFACE, fg=MUTED, justify="left", anchor="w", font=(ui_font_family(), 10)).pack(anchor="w", pady=(8, 0))
        return card

    def _choose_directory(self, variable: tk.StringVar) -> None:
        chosen = filedialog.askdirectory(initialdir=variable.get() or str(default_output_root()))
        if chosen:
            variable.set(chosen)

    def _choose_export_cookies_file(self) -> None:
        initial = self.export_cookies_path.get().strip()
        initial_dir = str(Path(initial).expanduser().parent) if initial else str(Path.home())
        chosen = filedialog.askopenfilename(
            title="选择 Wattpad Cookie 文件",
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
            messagebox.showinfo("没有可打开的位置", "当前还没有可打开的输出位置。")
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
        self.export_button.configure(state=state)

    def _run_in_worker(self, label: str, fn) -> None:
        if self.worker_running:
            messagebox.showinfo("任务进行中", "当前已有任务在运行，请等待完成后再操作。")
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
        self.current_selected_story = stories[0] if stories else None

        self.search_total_var.set(format_number(payload.get("total", 0)))
        self.search_returned_var.set(format_number(payload.get("returned", len(stories))))

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

    def _render_story_detail(self, story: dict | None) -> None:
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")

        if not story:
            self.detail_text.insert("end", "还没有可展示的结果。\n", "title")
            self.detail_text.insert("end", "\n搜索后这里会显示作品简介、标签、热度和链接。", "muted")
            self.detail_text.configure(state="disabled")
            return

        self.detail_text.insert("end", story["title"] + "\n", "title")
        self.detail_text.insert("end", f"作者：{story['author']}\n", "muted")
        self.detail_text.insert("end", "\n")

        fields = [
            ("阅读量", format_number(story["readCount"])),
            ("投票数", format_number(story["voteCount"])),
            ("评论数", format_number(story["commentCount"])),
            ("章节数", format_number(story["numParts"])),
            ("状态", status_text(story)),
            ("类型", type_text(story)),
            ("内容级别", maturity_text(story)),
            ("最后更新", story.get("lastPublishedPart") or "未知"),
            ("链接", story.get("url") or ""),
            ("标签", "、".join(story.get("tags", [])) or "无"),
        ]

        for label, value in fields:
            self.detail_text.insert("end", label + "：", "label")
            self.detail_text.insert("end", value + "\n")

        self.detail_text.insert("end", "\n简介\n", "label")
        self.detail_text.insert("end", story.get("description") or "暂无简介")
        self.detail_text.configure(state="disabled")

    def _on_result_select(self, _event=None) -> None:
        if not self.current_search_payload:
            return
        selected = self.results_tree.selection()
        if not selected:
            return
        index = int(selected[0])
        stories = self.current_search_payload.get("stories", [])
        if 0 <= index < len(stories):
            self.current_selected_story = stories[index]
            self._render_story_detail(self.current_selected_story)

    def _open_selected_story(self) -> None:
        if not self.current_selected_story:
            messagebox.showinfo("未选择作品", "请先在搜索结果中选中一条作品。")
            return
        open_target(self.current_selected_story["url"])

    def _copy_selected_story_url(self) -> None:
        if not self.current_selected_story:
            messagebox.showinfo("未选择作品", "请先在搜索结果中选中一条作品。")
            return
        url = self.current_selected_story["url"]
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.status_var.set("已复制选中作品链接")

    def _use_selected_story_url(self) -> None:
        if not self.current_selected_story:
            messagebox.showinfo("未选择作品", "请先在搜索结果中选中一条作品。")
            return
        self.export_story_url.set(self.current_selected_story["url"])
        if not self.export_basename.get().strip():
            self.export_basename.set(slugify(self.current_selected_story["title"]))
        self.notebook.select(self.export_tab)
        self.status_var.set("已把作品链接带入导出页")

    def _suggest_export_basename(self, story_url: str) -> str:
        raw = self.export_basename.get().strip()
        if raw:
            return slugify(raw) or "wattpad-export"

        tail = story_url.rstrip("/").split("/")[-1]
        tail = re.sub(r"^\d+-", "", tail)
        suggested = slugify(tail) or "wattpad-export"
        self.export_basename.set(suggested)
        return suggested

    def _ask_export_archive_path(self, suggested_basename: str) -> Path | None:
        initial_target = self.last_output_target or default_downloads_dir()
        initial_dir = initial_target if initial_target.is_dir() else initial_target.parent
        chosen = filedialog.asksaveasfilename(
            title="选择导出压缩包保存位置",
            initialdir=str(initial_dir),
            initialfile=f"{suggested_basename}.zip",
            defaultextension=".zip",
            filetypes=[("ZIP 压缩包", "*.zip")],
        )
        if not chosen:
            return None
        return normalize_zip_path(chosen)

    def _start_search(self) -> None:
        keyword = self.search_keyword.get().strip()
        if not keyword:
            messagebox.showerror("缺少关键词", "请先输入要搜索的关键词。")
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
                self.events.put(("status", f"搜索完成，已保存 {len(saved_paths)} 个结果文件"))
            else:
                self.events.put(("status", "搜索完成，未额外保存结果文件"))

        self._run_in_worker("正在搜索作品...", worker)

    def _start_export(self) -> None:
        story_url = self.export_story_url.get().strip()
        if not story_url:
            messagebox.showerror("缺少作品链接", "请先输入 Wattpad 作品链接。")
            return
        if not self.export_authorized.get():
            messagebox.showerror("缺少授权确认", "请先确认：该作品属于你，或你已经获得明确授权。")
            return

        basename = self._suggest_export_basename(story_url)
        archive_path = self._ask_export_archive_path(basename)
        if archive_path is None:
            self.status_var.set("已取消导出")
            return

        translate = self.export_translate.get()

        def worker() -> None:
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="wattpad-export-") as temp_root:
                staging_root = Path(temp_root) / archive_path.stem
                staging_root.mkdir(parents=True, exist_ok=True)

                session = build_session()
                try:
                    cookies_raw = self.export_cookies_path.get().strip()
                    cookies_path = Path(cookies_raw).expanduser().resolve() if cookies_raw else None
                    result = export_authorized_story(
                        session=session,
                        story_url=story_url,
                        output_dir=staging_root,
                        basename=basename,
                        translate_to_chinese=translate,
                        cookies_path=cookies_path,
                    )
                finally:
                    session.close()

                export_files = [Path(result["english_docx"])]
                if "chinese_docx" in result:
                    export_files.append(Path(result["chinese_docx"]))
                bundle_export_files(export_files, archive_path)

            print(f"English DOCX: {result['english_docx']}")
            if "chinese_docx" in result:
                print(f"Chinese DOCX: {result['chinese_docx']}")
            print(f"ZIP: {archive_path}")

            self.events.put(("output-target", archive_path))
            self.events.put(("status", f"导出完成，已打包 {len(export_files)} 个 Word 文档"))

        self._run_in_worker("正在导出作品...", worker)

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
                    self.status_var.set("任务失败")
                    self._append_log(payload)
                    messagebox.showerror("任务失败", payload)
        except queue.Empty:
            pass
        finally:
            self.root.after(120, self._poll_events)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-test", action="store_true")
    args, _ = parser.parse_known_args()

    if args.self_test:
        print("Wattpad 中文工具箱 GUI 自检通过")
        print(f"Python: {sys.version.split()[0]}")
        print(f"Platform: {sys.platform}")
        return 0

    root = tk.Tk()
    WattpadApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
