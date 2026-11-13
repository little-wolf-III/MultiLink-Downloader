"""真实 Tk 窗口操作测试；需要图形显示环境，仅网卡发现使用测试接口。"""
import json
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_download import Interface, MIB, Server
import gui


def main():
    """执行启动、候选切换、暂停、续传、限流和关闭保存测试。"""
    server = Server(size=12*MIB)
    cards = [Interface(i+1, f"127.0.0.{i+2}", f"模拟网卡{i+1}") for i in range(4)]
    gui.interfaces = lambda: cards
    root = tk.Tk()
    errors, passed = [], []
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))
    app = gui.App(root)
    started = time.monotonic()
    def wait(predicate, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root.update()
            if errors:
                raise AssertionError(errors)
            if predicate():
                return
            time.sleep(.02)
        raise AssertionError("界面等待超时：" + app.status.get())
    try:
        with tempfile.TemporaryDirectory() as temp:
            wait(lambda: len(app.cards) == 4 and not app.busy)
            passed.append("真实 Tk 启动及自动刷新四张候选网卡")
            row = app.table.bbox("1", "use")
            app.table.event_generate("<Button-1>", x=row[0]+10, y=row[1]+8)
            root.update()
            assert 1 not in app.enabled
            app.table.event_generate("<Button-1>", x=row[0]+10, y=row[1]+8)
            root.update()
            assert 1 in app.enabled
            passed.append("点击候选列排除与重新启用网卡")
            output = Path(temp) / "gui.bin"
            app.url.set(server.url())
            app.output.set(str(output))
            app.count.set("4")
            app.start_button.invoke()
            wait(lambda: app.job and sum(app.job.bytes_by_link.values()) >= MIB)
            app.pause_button.invoke()
            wait(lambda: not app.busy)
            assert "已暂停" in app.status.get(), app.status.get()
            assert Path(str(output)+".multilink.json").exists()
            passed.append("按钮启动四路下载并暂停保存断点")
            app.count.set("2")
            app.start_button.invoke()
            wait(lambda: not app.busy)
            assert output.read_bytes() == server.payload
            assert app.progress["value"] == 100
            report = json.loads(Path(str(output)+".report.json").read_text())
            assert report["resumed_bytes"] > 0
            passed.append("界面改为两路续传并完成；逐字节比对正确")
            # 截图仅用于内部排版检查，路径由测试调用方显式提供。
            if len(sys.argv) > 1:
                root.update_idletasks()
                root.update()
                import subprocess
                subprocess.run(['import', '-window', str(root.winfo_id()), sys.argv[1]], check=True)
            app.url.set(server.url("rate"))
            app.output.set(str(Path(temp)/"rate.bin"))
            app.start_button.invoke()
            wait(lambda: not app.busy)
            assert "429" in app.status.get(), app.status.get()
            passed.append("界面显示 HTTP 429，任务停止后按钮恢复")
            app.url.set(server.url())
            app.output.set(str(Path(temp)/"close.bin"))
            app.count.set("1")
            app.start_button.invoke()
            wait(lambda: app.job and sum(app.job.bytes_by_link.values()) >= MIB)
            app.close()
            # idle 事件会销毁窗口，后台必须先保存断点并释放锁。
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                root.update()
                if not app.busy:
                    break
                time.sleep(.02)
            assert not app.busy
            assert Path(temp, "close.bin.multilink.json").exists()
            assert not Path(temp, "close.bin.lock").exists()
            passed.append("下载中关闭窗口：先停止保存再退出")
            assert not errors, errors
    finally:
        server.close()
        try:
            root.destroy()
        except tk.TclError:
            pass
    result = dict(passed=True, checks=passed, callback_errors=errors,
                  seconds=round(time.monotonic()-started, 3), platform=sys.platform,
                  scope="真实 Tk + 真实 HTTP；网卡枚举替换为四个回环接口")
    Path(__file__).with_name("gui_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
