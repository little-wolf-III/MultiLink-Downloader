"""v2.2 中英双语界面；后台只发事件，所有 Tk 操作均在主线程执行。"""
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk
from urllib.parse import unquote, urlsplit

from downloader import Cancelled, Downloader, MIB
from network import interfaces
from i18n import LANGUAGES, Preferences, backend_text, text


class App:
    """输入 Tk 根窗口和可选配置路径，创建支持实时语言切换的下载界面。"""
    def __init__(self, root, settings_path=None):
        self.root, self.events = root, queue.Queue()
        self.preferences = Preferences(settings_path)
        self.language = self.preferences.load()
        self.labels, self.logs, self.card_states = [], [], {}
        self.status_state, self.summary_state = ("discover", {}), ("ready", {})
        self.cards, self.enabled = {}, set()
        self.busy, self.closing, self.job = False, False, None
        self.stop = threading.Event()
        self.last_bytes, self.last_time = {}, 0
        self.url, self.output, self.sha = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.count, self.status, self.summary = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.language_choice = tk.StringVar(value=next(k for k, v in LANGUAGES.items() if v == self.language))
        root.geometry("900x740")
        root.minsize(780, 650)
        root.protocol("WM_DELETE_WINDOW", self.close)
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
        style.configure("Horizontal.TProgressbar", background="#287db5", troughcolor="#e5e9ed")
        box = ttk.Frame(root, padding=16)
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=1)
        header = ttk.Frame(box)
        header.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.label(header, "heading", font=(self.font_family, 17, "bold")).pack(side="left")
        self.language_box = ttk.Combobox(header, textvariable=self.language_choice,
                                        values=tuple(LANGUAGES), width=10, state="readonly")
        self.language_box.pack(side="right")
        self.language_box.bind("<<ComboboxSelected>>", self.change_language)
        ttk.Label(header, text="语言 / Language").pack(side="right", padx=8)
        self.label(box, "subtitle").grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 14))
        self.inputs = []
        self.field(box, 2, "url", self.url)
        self.field(box, 3, "output", self.output)
        self.browse = self.button(box, "browse", self.choose_output)
        self.browse.grid(row=3, column=2, padx=(8, 0))
        self.inputs.append(self.browse)
        self.field(box, 4, "sha", self.sha)
        controls = ttk.Frame(box)
        controls.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(12, 8))
        self.label(controls, "count").pack(side="left")
        self.count_box = ttk.Combobox(controls, textvariable=self.count,
                                     values=(self.t("auto"), "1", "2", "3", "4"), width=5, state="readonly")
        self.count_box.current(0)
        self.count_box.pack(side="left", padx=8)
        self.label(controls, "count_hint").pack(side="left")
        self.refresh_button = self.button(controls, "refresh", self.refresh)
        self.refresh_button.pack(side="right")
        self.inputs.append(self.refresh_button)
        self.table = ttk.Treeview(box, columns=("use", "index", "name", "ip", "speed", "state"), show="headings", height=5)
        for name, width in [("use", 50), ("index", 55), ("name", 155), ("ip", 135), ("speed", 80), ("state", 300)]:
            self.table.column(name, width=width, minwidth=40, stretch=name in ("name", "state"))
        self.table.grid(row=6, column=0, columnspan=3, sticky="nsew")
        self.table.bind("<Button-1>", self.toggle)
        hint = self.label(box, "hint", wraplength=850)
        hint.grid(row=7, column=0, columnspan=3, sticky="w", pady=(5, 12))
        self.progress = ttk.Progressbar(box, maximum=100)
        self.progress.grid(row=8, column=0, columnspan=3, sticky="ew")
        ttk.Label(box, textvariable=self.summary).grid(row=9, column=0, columnspan=3, sticky="w", pady=5)
        status_label = ttk.Label(box, textvariable=self.status, wraplength=850)
        status_label.grid(row=10, column=0, columnspan=3, sticky="w")
        box.bind("<Configure>", lambda e: [w.configure(wraplength=max(200, e.width - 32)) for w in (hint, status_label)])
        buttons = ttk.Frame(box)
        buttons.grid(row=11, column=0, columnspan=3, sticky="ew", pady=10)
        self.start_button = self.button(buttons, "start", self.start)
        self.start_button.pack(side="left")
        self.pause_button = self.button(buttons, "pause", self.pause, state="disabled")
        self.pause_button.pack(side="left", padx=8)
        self.button(buttons, "folder", self.open_folder).pack(side="right")
        log_frame = ttk.Frame(box)
        log_frame.grid(row=12, column=0, columnspan=3, sticky="nsew")
        box.rowconfigure(12, weight=1)
        self.log_box = tk.Text(log_frame, height=7, wrap="word", state="disabled", font=(self.font_family, 10))
        scroll = ttk.Scrollbar(log_frame, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_box.pack(fill="both", expand=True)
        self.render_language()
        self.poll_id = root.after(100, self.poll)
        self.refresh_id = root.after(150, self.refresh)

    def t(self, key, **values):
        """读取当前语言文字；仅错误正文使用后端翻译，URL/名称/路径保持原样。"""
        if "message" in values:
            values["message"] = backend_text(values["message"], self.language)
        return text(key, self.language, **values)

    def label(self, parent, key, **options):
        """创建并登记可切换语言的标签。"""
        widget = ttk.Label(parent, text=self.t(key), **options)
        self.labels.append((widget, key))
        return widget

    def button(self, parent, key, command, **options):
        """创建并登记可切换语言的按钮。"""
        widget = ttk.Button(parent, text=self.t(key), command=command, **options)
        self.labels.append((widget, key))
        return widget

    def field(self, parent, row, key, variable):
        """增加一个标签和输入框，不翻译用户输入。"""
        self.label(parent, key).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        self.inputs.append(entry)

    def show_status(self, key, **values):
        """保存状态的语义和参数，让切换语言时能立即重绘。"""
        self.status_state = key, values
        self.status.set(self.t(key, **values))

    def show_summary(self, key, **values):
        """保存并显示总进度文字。"""
        self.summary_state = key, values
        self.summary.set(self.t(key, **values))

    def set_card_state(self, index, key=None, message=""):
        """保存网卡状态；后端原消息和界面文字键分开处理。"""
        self.card_states[index] = key, message
        value = self.t(key) if key else backend_text(message, self.language)
        self.table.set(str(index), "state", value)

    def log(self, raw_message, key=None, **values):
        """保留最多 600 条原始消息，允许既有日志随语言重绘。"""
        self.logs.append((key, values if key else str(raw_message)))
        self.logs = self.logs[-600:]
        self.render_logs()

    def render_logs(self):
        """将保留的原消息按当前语言展示，不改变后台日志和报告。"""
        lines = [self.t(key, **value) if key else backend_text(value, self.language) for key, value in self.logs]
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.insert("end", "\n".join(lines) + ("\n" if lines else ""))
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def render_language(self):
        """更新全部显示文字，保留候选选择、数量、输入和按钮启用状态。"""
        count = max(0, self.count_box.current())
        self.root.title(self.t("title"))
        for widget, key in self.labels:
            widget.configure(text=self.t(key))
        self.count_box.configure(values=(self.t("auto"), "1", "2", "3", "4"))
        self.count_box.current(count)
        for name in self.table["columns"]:
            self.table.heading(name, text=self.t(name))
        self.status.set(self.t(self.status_state[0], **self.status_state[1]))
        self.summary.set(self.t(self.summary_state[0], **self.summary_state[1]))
        for index, (key, message) in list(self.card_states.items()):
            self.set_card_state(index, key, message)
        self.render_logs()

    def change_language(self, event=None):
        """实时切换并记住偏好，下载线程无需停止或重建。"""
        self.language = LANGUAGES[self.language_choice.get()]
        self.render_language()
        try:
            self.preferences.save(self.language)
        except OSError as exc:
            self.log("", key="preferences_error", message=str(exc))

    def set_busy(self, busy, pausable=False):
        """控制任务参数编辑状态；语言选择始终可用。"""
        self.busy = busy
        for widget in self.inputs:
            widget.configure(state="disabled" if busy else "normal")
        self.count_box.configure(state="disabled" if busy else "readonly")
        self.start_button.configure(state="disabled" if busy else "normal")
        self.pause_button.configure(state="normal" if pausable else "disabled")

    def refresh(self):
        """后台枚举系统网卡，避免 PowerShell 卡住界面。"""
        if self.busy:
            return
        self.set_busy(True)
        self.show_status("discover")
        def work():
            try:
                self.events.put(("cards", interfaces()))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("idle", None))
        threading.Thread(target=work, daemon=True).start()

    def toggle(self, event):
        """点击候选列切换网卡，任务中禁止修改。"""
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
        """从 URL 生成安全文件名，缺少名称时使用 download.bin。"""
        name = unquote(urlsplit(self.url.get().strip()).path.rsplit("/", 1)[-1]) or "download.bin"
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        name = name.rstrip(". ")[:180] or "download.bin"
        if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
            name = "download_" + name
        return name

    def choose_output(self):
        """选择保存位置；标题随界面语言，系统对话框内置按钮随操作系统。"""
        downloads = Path.home() / "Downloads"
        path = filedialog.asksaveasfilename(title=self.t("save_title"), initialfile=self.suggested_name(),
                                          initialdir=str(downloads if downloads.exists() else Path.home()))
        if path:
            self.output.set(path)

    def start(self):
        """读取界面参数并启动后端；链路数量采用选项索引而非翻译文字。"""
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
                messagebox.showerror(self.t("invalid"), self.t("no_cards"))
                return
            self.job = Downloader(self.url.get(), self.output.get().strip(), cards, self.count_box.current(),
                                  lambda kind, data: self.events.put((kind, data)), self.stop, self.sha.get())
        except Exception as exc:
            messagebox.showerror(self.t("invalid"), backend_text(str(exc), self.language))
            return
        self.set_busy(True, True)
        self.progress["value"] = 0
        self.last_bytes, self.last_time = {}, 0
        self.show_status("prepare")
        self.show_summary("preparing")
        self.log("", key="new_job")
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
        """请求暂停，后台完成保存后再恢复按钮。"""
        self.stop.set()
        self.pause_button.configure(state="disabled")
        self.show_status("stopping")

    def poll(self):
        """在主线程消费后端事件，使用当前语言绘制状态及日志。"""
        for _ in range(100):
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            match kind:
                case "cards":
                    self.cards = {it.index: it for it in data}
                    self.enabled, self.card_states = set(self.cards), {}
                    self.table.delete(*self.table.get_children())
                    for it in data:
                        self.table.insert("", "end", iid=str(it.index), values=("✓", it.index, it.name, it.ip, "—", ""))
                        self.set_card_state(it.index, "pending")
                    self.show_status("cards", count=len(data))
                case "interface":
                    self.set_card_state(data["index"], message=data["status"])
                    if "speed" in data:
                        self.table.set(str(data["index"]), "speed", f"{data['speed']/MIB:.2f}")
                case "selected":
                    selected = {it["index"] for it in data}
                    for index in self.enabled:
                        self.set_card_state(index, "selected" if index in selected else "unused")
                    self.show_status("routes", names=" + ".join(it["name"] for it in data))
                case "progress":
                    now = time.monotonic()
                    self.progress["value"] = 100 * data["done"] / max(1, data["total"])
                    self.show_summary("progress", percent=self.progress["value"], done=data["done"]/MIB,
                                      total=data["total"]/MIB, speed=data["speed"]/MIB)
                    if self.last_time:
                        for index, size in data["per_link"].items():
                            rate = (size - self.last_bytes.get(index, 0)) / max(0.01, now - self.last_time)
                            self.table.set(str(index), "speed", f"{rate/MIB:.2f}")
                    self.last_bytes, self.last_time = data["per_link"], now
                case "log":
                    self.log(data)
                case "done":
                    self.show_status("complete", sha=data["sha256"])
                    self.log("", key="complete", sha=data["sha256"])
                case "paused":
                    self.show_status("paused")
                    self.log("", key="paused")
                case "error":
                    self.show_status("error", message=data)
                    self.log("", key="error", message=data)
                case "idle":
                    self.set_busy(False)
                    if self.closing:
                        self.destroy()
                        return
        self.poll_id = self.root.after(100, self.poll)

    def open_folder(self):
        """打开保存目录；文件系统错误使用当前界面语言的错误框提示。"""
        folder = Path(self.output.get()).expanduser().parent if self.output.get() else Path.home()
        try:
            if os.name == "nt" and folder.exists():
                os.startfile(str(folder.resolve()))
        except OSError as exc:
            messagebox.showerror(self.t("title"), self.t("error", message=str(exc)))

    def destroy(self):
        """取消界面定时器后销毁窗口。"""
        for identifier in (self.poll_id, self.refresh_id):
            self.root.after_cancel(identifier)
        self.root.destroy()

    def close(self):
        """有任务先请求停止保存；空闲时直接退出。"""
        if self.busy:
            self.closing = True
            self.pause()
        else:
            self.destroy()


def main():
    """创建并运行双语窗口。"""
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
