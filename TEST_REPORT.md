# v2.2 验证报告 / Validation report

日期 / Date (Japan): 2026-11-14

## 本次修改 / Changes

- `gui.py`：中英界面实时切换；状态、进度、已有日志同步重绘。
- `i18n.py`：新增界面字典、已知后端消息翻译、语言偏好保存。
- `README.md`、`README.en.md`：语言功能与升级说明。
- `network.py`、`downloader.py` 与 GitHub 下载的 v2.1 基线 **SHA-256 完全相同**。
- `LICENSE` 保留当前仓库原文，未修改许可。

The GUI now supports live Chinese/English switching. A separate translation module stores UI strings, translates known backend messages for display, and remembers the language preference. The download and network backends are byte-for-byte unchanged from the v2.1 baseline. The repository's existing license is preserved.

## 测试结果 / Results

**6 项翻译/配置测试 + 10 项真实 Tk 操作检查通过。**

**6 translation/preference tests and 10 real Tk UI checks passed.**

翻译与配置 / Translation and preferences:

1. 所有界面文字键可在两种语言中正确格式化；English 字典无中文残留。
2. 所有已知后端固定消息翻译正确。
3. 16 类动态/嵌套消息翻译，包括 HTTP 错误、测速、续传、写盘失败等。
4. 用户自定义网卡名及未知操作系统诊断原文保留。
5. 中英文偏好保存和重新读取正确。
6. 缺失、损坏或无效配置自动使用系统默认语言。

真实界面操作 / Real UI checks:

1. 切换中英保留 URL、文件路径、指定数量和候选网卡；保存语言偏好。
2. “自动 / Auto”切换后仍对应同一个参数 0。
3. 参数错误框、文件选择框标题使用当前语言。
4. 探测及下载中切换语言，任务对象不变，已有后端日志立即重译。
5. 英文界面暂停并保存断点，切回中文后状态与日志同步更新。
6. 中文界面改为两路续传，完成文件与服务端原始 12 MiB 数据逐字节一致；切换英文保留进度及 SHA-256。
7. 真实 HTTP 429 错误能显示英文，并可重新切回中文。
8. 网卡自定义中文名称不被翻译或丢失。
9. 英文下载中关闭窗口，先保存断点并释放锁再退出。
10. 新建窗口后读取上次保存的 English 偏好。

下载使用真实绑定套接字和 HTTP Range 服务，网卡发现替换成四个回环源 IP。
测试中没有发现 Tk 回调异常。已目视检查中英文窗口排版。

Download integration used real bound sockets and an HTTP Range server, with adapter discovery replaced by four loopback source IPs. Completed data was compared byte-for-byte with the server payload. No Tk callback errors occurred. Both language layouts were visually inspected.

## 范围与限制 / Scope and limitations

- Linux，标准库测试；GUI 使用 Python 3.13.5 / Tk 8.6 和虚拟 X 显示。
- 本次没有重复运行旧版全部后端测试；后端未改动，并做了源码哈希核对及上述 GUI 下载/续传集成验证。
- Windows 物理网卡识别、系统语言探测及 `IP_UNICAST_IF` 实际出站路由仍需 Windows 实机确认。
- 操作系统原生文件选择框的内置按钮由系统决定语言，不由此界面切换。
- CLI 和 JSON 报告继续保留后端原格式及文字；报告/断点内的版本 2.1 表示后端兼容格式，并非窗口版本。
- 网卡名称、文件路径、外部系统原始错误不会被强行翻译。

The complete old backend suite was not rerun for this UI-only release. Backend identity was verified by hashes, and real download/resume integration was tested through the bilingual GUI. Windows physical networking and Windows UI-language detection still require local verification. Native dialogs follow OS language; CLI output and backend report formats remain unchanged.

## 证据与复测 / Evidence and reproduction

- `tests/v22_results.json`：结果汇总和交付源码哈希。
- `tests/gui_results.json`、`tests/gui_run.log`：真实界面检查结果。
- `tests/i18n_run.log`：6 项翻译/配置测试原始输出。
- `tests/test_i18n.py`：`python tests/test_i18n.py`
- `tests/test_gui.py`：Linux 有图形显示时执行 `python tests/test_gui.py`。
- `tests/test_download.py`：保留 v2.1 的 24 项后端测试脚本，可在 Linux 上独立运行。

The network integration scripts use Linux loopback source addresses; they are not a Windows physical-adapter test harness. Screenshots used for internal layout inspection are not required to run the application.
