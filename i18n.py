"""界面中英文字典与语言偏好；仅翻译显示文字，不修改下载后端。"""
import json
import locale
import os
import re
from pathlib import Path

LANGUAGES = {"简体中文": "zh", "English": "en"}

# 每项依次为中文和英文；使用稳定的键，避免业务状态依赖显示语言。
TEXTS = {
    "title": ("多链路下载器 v2.2", "MultiLink Downloader v2.2"),
    "heading": ("多链路下载器  2.2", "MultiLink Downloader  2.2"),
    "subtitle": ("自动识别 · 目标测速 · 最多四路 · 断点续传", "Auto-detect · Benchmark · Up to 4 adapters · Resume"),
    "url": ("下载直链", "Direct file URL"),
    "output": ("保存文件", "Save to"),
    "sha": ("SHA-256（可选）", "SHA-256 (optional)"),
    "browse": ("浏览…", "Browse…"),
    "count": ("使用链路数", "Adapter count"),
    "auto": ("自动", "Auto"),
    "count_hint": ("自动选组合，或指定数量", "Auto-select, or choose an exact count"),
    "refresh": ("刷新网卡", "Refresh adapters"),
    "use": ("候选", "Use"),
    "index": ("编号", "Index"),
    "name": ("网卡", "Adapter"),
    "ip": ("IPv4", "IPv4"),
    "speed": ("MiB/s", "MiB/s"),
    "state": ("状态", "Status"),
    "hint": ("点击“候选”列启用/排除网卡。超过四张时，在单路最快四张中比较组合。",
             "Click Use to include/exclude an adapter. With more than 4 candidates, combinations use the fastest 4 individual adapters."),
    "start": ("开始 / 续传", "Start / Resume"),
    "pause": ("暂停", "Pause"),
    "folder": ("打开保存目录", "Open folder"),
    "ready": ("就绪", "Ready"),
    "discover": ("正在识别系统网卡……", "Detecting network adapters…"),
    "cards": ("已识别 {count} 张 IPv4 网卡。输入文件直链并开始。", "Found {count} IPv4 adapters. Enter a direct file URL to start."),
    "pending": ("待探测", "Not checked"),
    "selected": ("已选用", "Selected"),
    "unused": ("未选用", "Not selected"),
    "routes": ("下载链路：{names}", "Download adapters: {names}"),
    "prepare": ("正在探测、测速和选择组合；慢线路可能需要更久……", "Checking the target and benchmarking combinations; slow routes may take longer…"),
    "preparing": ("准备中；测速会消耗流量", "Preparing; benchmarks use network data"),
    "stopping": ("正在停止并保存断点，请稍候；网络请求可能需要等待超时……", "Stopping and saving progress; pending network requests may need to time out…"),
    "paused": ("已暂停。点击“开始 / 续传”继续；准备阶段暂停不会生成断点。", "Paused. Click Start / Resume to continue. Pausing during preparation creates no resume file."),
    "complete": ("下载完成，SHA-256：{sha}", "Complete. SHA-256: {sha}"),
    "error": ("任务停止：{message}", "Task stopped: {message}"),
    "new_job": ("—— 新任务：v2.2 ——", "—— New task: v2.2 ——"),
    "invalid": ("参数有误", "Invalid settings"),
    "no_cards": ("请先刷新并启用至少一张候选网卡。", "Refresh adapters and enable at least one candidate."),
    "save_title": ("选择保存文件（已有文件不会覆盖）", "Choose destination (existing files will not be overwritten)"),
    "progress": ("{percent:.1f}%   {done:.2f} / {total:.2f} MiB   总速度 {speed:.2f} MiB/s",
                 "{percent:.1f}%   {done:.2f} / {total:.2f} MiB   Total {speed:.2f} MiB/s"),
    "preferences_error": ("语言已切换，但无法保存偏好：{message}", "Language changed, but the preference could not be saved: {message}"),
}

# 对 v2.1 后端的完整消息做显示层翻译，保持原始错误、报告和断点不变。
BACKEND = {
    "目标可达": "Target reachable",
    "可用": "Available",
    "未提供": "not provided",
    "自动识别网卡需要 Windows 10/11；云端测试使用模拟接口。": "Adapter auto-detection requires Windows 10/11; cloud tests use simulated interfaces.",
    "请输入 HTTP/HTTPS 文件直链，不支持带用户名密码的 URL。": "Enter a direct HTTP/HTTPS file URL. URLs containing usernames or passwords are not supported.",
    "跳转缺少 Location。": "Redirect response has no Location header.",
    "拒绝 HTTPS 降级到 HTTP。": "Refusing an HTTPS-to-HTTP downgrade.",
    "重定向次数过多。": "Too many redirects.",
    "Content-Range 与请求不一致，拒绝拼接。": "Content-Range does not match the request; refusing to combine data.",
    "文件范围或编码异常。": "Invalid file range or content encoding.",
    "Content-Length 与请求不一致。": "Content-Length does not match the request.",
    "文件版本发生变化，已停止，拒绝混合不同版本。": "The file version changed. Stopped to avoid mixing versions.",
    "下载分块提前断开，将重试。": "The chunk transfer ended early; retrying.",
    "链路数量只能是自动（0）或 1～4。": "Adapter count must be Auto (0) or 1–4.",
    "每路连接数为 1～4，分块大小为 64/128/256/512 KiB。": "Use 1–4 connections per adapter and a chunk size of 64/128/256/512 KiB.",
    "预期 SHA-256 应为 64 位十六进制字符。": "Expected SHA-256 must contain 64 hexadecimal characters.",
    "没有可用候选网卡。请连接网络，刷新网卡并勾选。": "No candidate adapters. Connect to a network, refresh adapters, and enable candidates.",
    "所有候选网卡均无法下载目标。请查看网卡状态和直链是否有效。": "No candidate can download this target. Check adapter status and the direct file URL.",
    "候选超过四张：仅在单路实测最快的四张中比较组合。": "More than four candidates: comparing combinations of the four fastest individual adapters.",
    "没有成功完成测速的组合。": "No combination completed its benchmark successfully.",
    "断点文件不完整：.part 与 .multilink.json 必须成对保留；请换保存名。": "Incomplete resume files: keep both .part and .multilink.json, or choose a new destination.",
    "目标没有可靠版本标识，不能安全跨运行续传；请换保存名重新下载。": "The target has no reliable version identifier. Cross-session resume is unsafe; choose a new destination and restart.",
    "断点的地址、文件版本或分块大小不一致；请换保存名。": "Resume URL, file version, or chunk size does not match; choose a new destination.",
    "断点文件大小异常；请换保存名。": "Unexpected partial-file size; choose a new destination.",
    "断点记录中的块编号异常。": "Invalid chunk index in the resume metadata.",
    "仍有未完成分块，断点已保存。": "Some chunks are incomplete. Resume progress has been saved.",
    "目标文件已存在，请更换保存文件名。": "The destination already exists. Choose another filename.",
    "同名任务正在运行或上次异常退出留下 .lock；确认无任务运行后删除该锁文件。": "This destination is locked by another task or a stale .lock file. Remove the lock only after confirming no task is using it.",
    "下载完成，正在校验完整 SHA-256……": "Download finished. Calculating the full SHA-256…",
    "SHA-256 与预期不符，保留 .part，未发布最终文件。": "SHA-256 does not match the expected value. The .part file is retained; no final file was published.",
}

# 元组：匹配式、英文格式、需要递归翻译的捕获组；网卡名称等用户数据原样保留。
PATTERNS = [
    (r"正在探测 (\d+) 张候选网卡……", "Checking {0} candidate adapters…", ()),
    (r"测速 (.*)：([\d.]+) MiB/s", "Benchmark {0}: {1} MiB/s", ()),
    (r"指定 (\d+) 条链路，但只有 (\d+) 条通过探测和测速。", "Requested {0} adapters, but only {1} passed checks and benchmarks.", ()),
    (r"(.*) 返回的文件与其他线路不同，拒绝拼接。", "{0} returned a different file; refusing to combine data.", ()),
    (r"已选择：(.*)", "Selected: {0}", ()),
    (r"续传校验完成：保留 ([\d.]+) MiB，损坏块自动重下。", "Resume verified: keeping {0} MiB; corrupted chunks will be downloaded again.", ()),
    (r"开始下载；每路 (\d+) 个连接。", "Starting download with {0} connections per adapter.", ()),
    (r"HTTP (\d+)，目标必须支持 Range 并返回 206。", "HTTP {0}: the target must support Range requests and return 206.", ()),
    (r"HTTP 429：已停止整个任务，Retry-After=(.*)；稍后手动续传。", "HTTP 429: the entire task has stopped. Retry-After={0}. Resume manually later.", (0,)),
    (r"(.*) 连接失败：(.*)", "{0} connection failed: {1}", (1,)),
    (r"(.*) 连续失败，已停用：(.*)", "{0} disabled after repeated failures: {1}", (1,)),
    (r"不可用：(.*)", "Unavailable: {0}", (0,)),
    (r"测速失败：(.*)", "Benchmark failed: {0}", (0,)),
    (r"组合测速失败，跳过：(.*)", "Skipping failed combination: {0}", (0,)),
    (r"文件写入失败，请检查磁盘空间和权限：(.*)", "File write failed; check disk space and permissions: {0}", (0,)),
    (r"链路持续失败，已保存断点：(.*)", "Routes keep failing; resume progress saved: {0}", (0,)),
]


def text(key, language, **values):
    """输入文字键、语言和格式参数，输出显示文字。"""
    return TEXTS[key][1 if language == "en" else 0].format(**values)


def backend_text(message, language):
    """翻译已知后端消息；未知系统错误及用户名称保留原文，避免丢失诊断信息。"""
    message = str(message)
    if language != "en":
        return message
    if message in BACKEND:
        return BACKEND[message]
    for pattern, template, nested in PATTERNS:
        match = re.fullmatch(pattern, message, flags=re.DOTALL)
        if match:
            values = list(match.groups())
            for index in nested:
                values[index] = backend_text(values[index], language)
            return template.format(*values)
    return message


def default_language():
    """首次启动跟随系统界面语言：中文用中文，其他语言用英文。"""
    if os.name == "nt":
        try:
            import ctypes
            return "zh" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3ff == 4 else "en"
        except (AttributeError, OSError):
            pass
    name = (locale.getlocale()[0] or "").lower()
    return "zh" if name.startswith(("zh", "chinese")) else "en"


class Preferences:
    """仅保存语言；默认写入用户配置目录，不修改源码目录。"""
    def __init__(self, path=None):
        base = Path(os.environ.get("APPDATA") or Path.home() / ".config")
        self.path = Path(path) if path else base / "MultiLinkDownloader" / "settings.json"

    def load(self):
        """读取有效的语言代码；文件缺失或损坏时使用系统默认值。"""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            language = data.get("language") if isinstance(data, dict) else None
            if language in ("zh", "en"):
                return language
        except (OSError, ValueError):
            pass
        return default_language()

    def save(self, language):
        """原子保存语言代码；失败由界面提示，但不打断下载。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps({"language": language}), encoding="utf-8")
        os.replace(temporary, self.path)
