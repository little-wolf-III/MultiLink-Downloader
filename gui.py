"""Tkinter 小界面；后台线程只发事件，所有控件更新都在主线程完成。"""
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk
from urllib.parse import unquote, urlsplit

from downloader import Cancelled, Downloader, MIB
from network import interfaces


class App:
    """输入 Tk 根窗口，构建网卡列表、参数区和进度区。"""
    def __init__(self, root):
        self.root, self.events = root, queue.Queue()
        self.cards, self.enabled = {}, set()
        self.busy, self.closing, self.job = False, False, None
        self.stop = threading.Event()
        self.last_bytes, self.last_time = {}, 0
        self.url, self.output, self.sha = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.count, self.status = tk.StringVar(value="自动"), tk.StringVar(value="正在识别网卡……")
        self.summary = tk.StringVar(value="就绪")
        root.title("多链路下载器 v2.1")
        root.geometry("890x720")
        root.minsize(760, 610)
        root.protocol("WM_DELETE_WINDOW", self.close)
        # Windows 使用微软雅黑；其他环境优先使用已安装的中文字体。
        families = set(font.families(root))
        self.font_family = next((name for name in ("Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC")
                                 if name in families), "TkDefaultFont")
        if self.font_family != "TkDefaultFont":
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
                font.nametofont(name).configure(family=self.font_family, size=10)
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Treeview", rowheight=26)
        box = ttk.Frame(root, padding=16)
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="多链路下载器  2.1", font=(self.font_family, 17, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(box, text="自动识别 · 目标测速 · 最多四路 · 断点续传").grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 14))
        self.inputs = []
        self.field(box, 2, "下载直链", self.url)
        self.field(box, 3, "保存文件", self.output)
        self.browse = ttk.Button(box, text="浏览…", command=self.choose_output)
        self.browse.grid(row=3, column=2, padx=(8, 0))
        self.inputs.append(self.browse)
        self.field(box, 4, "SHA-256（可选）", self.sha)
        controls = ttk.Frame(box)
        controls.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(12, 8))
        ttk.Label(controls, text="使用链路数").pack(side="left")
        self.count_box = ttk.Combobox(controls, textvariable=self.count, values=("自动", "1", "2", "3", "4"), width=6, state="readonly")
        self.count_box.pack(side="left", padx=8)
        ttk.Label(controls, text="自动：比较组合后选路；数字：严格使用指定数量").pack(side="left")
        self.refresh_button = ttk.Button(controls, text="刷新网卡", command=self.refresh)
        self.refresh_button.pack(side="right")
        self.inputs.append(self.refresh_button)
        self.table = ttk.Treeview(box, columns=("use", "index", "name", "ip", "speed", "status"), show="headings", height=6)
        for name, title, width in [("use", "候选", 45), ("index", "编号", 45), ("name", "网卡", 140),
                                   ("ip", "IPv4", 125), ("speed", "MiB/s", 80), ("status", "状态", 240)]:
            self.table.heading(name, text=title)
            self.table.column(name, width=width, minwidth=40, stretch=name in ("name", "status"))
        self.table.grid(row=6, column=0, columnspan=3, sticky="nsew")
        self.table.bind("<Button-1>", self.toggle)
        ttk.Label(box, text="点击“候选”列启用/排除网卡。默认全部参与探测；超过四张时，在单路最快四张中比较组合。",
                  wraplength=820).grid(row=7, column=0, columnspan=3, sticky="w", pady=(5, 12))
        self.progress = ttk.Progressbar(box, maximum=100)
        self.progress.grid(row=8, column=0, columnspan=3, sticky="ew")
        ttk.Label(box, textvariable=self.summary).grid(row=9, column=0, columnspan=3, sticky="w", pady=5)
        ttk.Label(box, textvariable=self.status, wraplength=820).grid(row=10, column=0, columnspan=3, sticky="w")
        buttons = ttk.Frame(box)
        buttons.grid(row=11, column=0, columnspan=3, sticky="ew", pady=10)
        self.start_button = ttk.Button(buttons, text="开始 / 续传", command=self.start)
        self.start_button.pack(side="left")
        self.pause_button = ttk.Button(buttons, text="暂停", command=self.pause, state="disabled")
        self.pause_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="打开保存目录", command=self.open_folder).pack(side="right")
        log_frame = ttk.Frame(box)
        log_frame.grid(row=12, column=0, columnspan=3, sticky="nsew")
        box.rowconfigure(12, weight=1)
        self.log_box = tk.Text(log_frame, height=8, wrap="word", state="disabled", font=(self.font_family, 10))
        scroll = ttk.Scrollbar(log_frame, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_box.pack(fill="both", expand=True)
        root.after(100, self.poll)
        root.after(150, self.refresh)

    def field(self, parent, row, title, variable):
        """增加一个标签及文本输入框。"""
        ttk.Label(parent, text=title).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        self.inputs.append(entry)

    def log(self, text):
        """在主线程追加日志，限制行数避免长期占内存。"""
        self.log_box.configure(state="normal")
        self.log_box.insert("end", str(text) + "\n")
        if int(self.log_box.index("end-1c").split(".")[0]) > 800:
            self.log_box.delete("1.0", "200.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def set_busy(self, busy, pausable=False):
        """统一设置控件状态，测速和下载时禁止改参数。"""
        self.busy = busy
        for widget in self.inputs:
            widget.configure(state="disabled" if busy else "normal")
        self.count_box.configure(state="disabled" if busy else "readonly")
        self.start_button.configure(state="disabled" if busy else "normal")
        self.pause_button.configure(state="normal" if pausable else "disabled")

    def refresh(self):
        """后台枚举系统网卡，避免 PowerShell 启动卡住界面。"""
        if self.busy:
            return
        self.set_busy(True)
        self.status.set("正在识别系统网卡……")
        def work():
            try:
                self.events.put(("cards", interfaces()))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("idle", None))
        threading.Thread(target=work, daemon=True).start()

    def toggle(self, event):
        """点击第一列切换候选网卡；不直接指定最终组合。"""
        if self.busy or self.table.identify_column(event.x) != "#1":
            return
        row = self.table.identify_row(event.y)
        if row:
            index = int(row)
            if index in self.enabled:
                self.enabled.remove(index)
            else:
                self.enabled.add(index)
            self.table.set(row, "use", "✓" if index in self.enabled else "")

    def suggested_name(self):
        """从直链生成安全文件名，缺少文件名时使用 download.bin。"""
        name = unquote(urlsplit(self.url.get().strip()).path.rsplit("/", 1)[-1]) or "download.bin"
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        name = name.rstrip(". ")[:180] or "download.bin"
        if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
            name = "download_" + name
        return name

    def choose_output(self):
        """选择保存位置；同一路径可继续 v2.1 断点。"""
        downloads = Path.home() / "Downloads"
        path = filedialog.asksaveasfilename(title="选择保存文件（已有文件不会覆盖）", initialfile=self.suggested_name(),
                                          initialdir=str(downloads if downloads.exists() else Path.home()))
        if path:
            self.output.set(path)

    def start(self):
        """从主线程读取参数，启动独立下载线程。"""
        if self.busy:
            return
        if not self.output.get().strip():
            self.choose_output()
            if not self.output.get().strip():
                return
        self.stop = threading.Event()
        try:
            cards = [it for index, it in self.cards.items() if index in self.enabled]
            if not cards:
                raise ValueError("请先刷新并启用至少一张候选网卡。")
            count = 0 if self.count.get() == "自动" else int(self.count.get())
            self.job = Downloader(self.url.get(), self.output.get().strip(), cards, count,
                                  lambda kind, data: self.events.put((kind, data)), self.stop, self.sha.get())
        except Exception as exc:
            messagebox.showerror("参数有误", str(exc))
            return
        self.set_busy(True, True)
        self.progress["value"] = 0
        self.last_bytes, self.last_time = {}, 0
        self.status.set("正在探测、测速和选择组合；最多约几十秒，慢线路可能更久……")
        self.summary.set("准备中；测速会消耗少量流量")
        self.log("—— 新任务：v2.1 ——")
        def work():
            try:
                self.job.run()
            except Cancelled:
                self.events.put(("paused", None))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("idle", None))
        threading.Thread(target=work, daemon=True).start()

    def pause(self):
        """请求停止，等待后台线程保存断点，不在界面线程阻塞等待。"""
        self.stop.set()
        self.pause_button.configure(state="disabled")
        self.status.set("正在停止并保存断点，请稍候；正在等待的网络请求会在超时后退出……")

    def poll(self):
        """处理后台事件；所有 Tk 控件操作均由此主线程执行。"""
        import time
        for _ in range(100):
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            match kind:
                case "cards":
                    self.cards = {it.index: it for it in data}
                    self.enabled = set(self.cards)
                    self.table.delete(*self.table.get_children())
                    for it in data:
                        self.table.insert("", "end", iid=str(it.index), values=("✓", it.index, it.name, it.ip, "—", "待探测"))
                    self.status.set(f"已识别 {len(data)} 张 IPv4 网卡。输入文件直链并开始。")
                case "interface":
                    row = str(data["index"])
                    self.table.set(row, "status", data["status"])
                    if "speed" in data:
                        self.table.set(row, "speed", f"{data['speed']/MIB:.2f}")
                case "selected":
                    selected = {it["index"] for it in data}
                    for index in self.cards:
                        if index in self.enabled:
                            self.table.set(str(index), "status", "已选用" if index in selected else "未选用")
                    self.status.set("下载链路：" + " + ".join(it["name"] for it in data))
                case "progress":
                    now = time.monotonic()
                    self.progress["value"] = 100 * data["done"] / max(1, data["total"])
                    self.summary.set(f"{self.progress['value']:.1f}%   {data['done']/MIB:.2f} / {data['total']/MIB:.2f} MiB   总速度 {data['speed']/MIB:.2f} MiB/s")
                    if self.last_time:
                        for index, size in data["per_link"].items():
                            rate = (size - self.last_bytes.get(index, 0)) / max(0.01, now - self.last_time)
                            self.table.set(str(index), "speed", f"{rate/MIB:.2f}")
                    self.last_bytes, self.last_time = data["per_link"], now
                case "log":
                    self.log(data)
                case "done":
                    self.status.set("下载完成，SHA-256：" + data["sha256"])
                    self.log("完成，SHA-256：" + data["sha256"])
                case "paused":
                    self.status.set("已暂停。再次点击“开始 / 续传”继续；准备阶段暂停不会生成断点。")
                    self.log(self.status.get())
                case "error":
                    self.status.set("任务停止：" + data)
                    self.log("错误：" + data)
                case "idle":
                    self.set_busy(False)
                    if self.closing:
                        self.root.destroy()
                        return
        self.root.after(100, self.poll)

    def open_folder(self):
        """打开保存目录；不会打开下载的可执行文件。"""
        folder = Path(self.output.get()).expanduser().parent if self.output.get() else Path.home()
        if os.name == "nt" and folder.exists():
            os.startfile(str(folder.resolve()))

    def close(self):
        """有任务时先停止保存，收到 idle 后再关闭窗口。"""
        if self.busy:
            self.closing = True
            self.pause()
        else:
            self.root.destroy()


def main():
    """创建并运行桌面窗口。"""
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
