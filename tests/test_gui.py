"""真实 Tk 双语操作与 HTTP 集成检查；只将网卡发现替换为四个回环接口。"""
import json
import re
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_download import Interface, MIB, Server
from i18n import Preferences
import gui


def main():
    server = Server(size=12*MIB)
    cards = [Interface(i+1, f"127.0.0.{i+2}", f"Adapter {i+1}") for i in range(4)]
    gui.interfaces = lambda: cards
    root = tk.Tk()
    errors, checks = [], []
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as temp:
        settings = Path(temp) / "settings.json"
        Preferences(settings).save("zh")
        app = gui.App(root, settings)
        def wait(predicate, timeout=45):
            deadline = time.monotonic()+timeout
            while time.monotonic() < deadline:
                root.update()
                assert not errors, errors
                if predicate():
                    return
                time.sleep(.02)
            raise AssertionError("UI timeout: " + app.status.get())
        def language(value):
            app.language_choice.set(value)
            app.language_box.event_generate("<<ComboboxSelected>>")
            root.update()
        def screenshot(name):
            if len(sys.argv)>1:
                import subprocess
                root.update_idletasks()
                subprocess.run(["import", "-window", str(root.winfo_id()), str(Path(sys.argv[1])/name)], check=True)
        try:
            wait(lambda: len(app.cards)==4 and not app.busy)
            assert app.start_button["text"] == "开始 / 续传"
            assert app.count_box.current()==0
            app.url.set(server.url())
            output = Path(temp)/"语言测试.bin"
            app.output.set(str(output))
            # 候选选择与数字不随文字翻译改变。
            app.count_box.current(4)
            app.enabled.remove(4)
            app.table.set("4", "use", "")
            language("English")
            assert app.language == "en" and Preferences(settings).load()=="en"
            assert app.start_button["text"] == "Start / Resume"
            assert app.count_box.current()==4 and app.enabled=={1,2,3}
            assert app.output.get()==str(output) and app.url.get()==server.url()
            assert "Found 4 IPv4" in app.status.get()
            assert app.table.heading("name")["text"]=="Adapter"
            checks.append("Chinese-to-English switch preserves URL, path, exact count and candidate selection; preference saved")
            app.count_box.current(0)
            language("简体中文")
            assert app.count.get()=="自动"
            language("English")
            assert app.count.get()=="Auto" and app.count_box.current()==0
            checks.append("Auto option maps to zero across both languages")
            # 参数错误框和文件选择框标题必须随语言变化。
            app.url.set("invalid")
            with patch.object(gui.messagebox, "showerror") as dialog:
                app.start()
                assert dialog.call_args.args[0]=="Invalid settings"
                assert "direct HTTP/HTTPS" in dialog.call_args.args[1]
            app.url.set(server.url())
            with patch.object(gui.filedialog, "asksaveasfilename", return_value="") as dialog:
                app.choose_output()
                assert dialog.call_args.kwargs["title"].startswith("Choose destination")
            checks.append("Validation messages and file-dialog title use the chosen language")
            app.enabled.add(4)
            app.table.set("4", "use", "✓")
            app.count_box.current(4)
            screenshot("english_ready.png")
            app.start_button.invoke()
            original_job = app.job
            wait(lambda: app.busy and len(app.logs)>1)
            language("简体中文")
            assert app.job is original_job and app.busy
            language("English")
            assert app.job is original_job and app.count_box.current()==4
            wait(lambda: sum(app.job.bytes_by_link.values())>=MIB)
            assert "Starting download" in app.log_box.get("1.0","end")
            assert not re.search(r"[\u4e00-\u9fff]",app.log_box.get("1.0","end"))
            checks.append("Switching languages during probing/downloading preserves the running job and translates prior backend logs")
            app.pause_button.invoke()
            wait(lambda:not app.busy)
            assert app.status.get().startswith("Paused.")
            assert Path(str(output)+".multilink.json").exists()
            language("简体中文")
            assert app.status.get().startswith("已暂停")
            assert "开始下载" in app.log_box.get("1.0","end")
            checks.append("English pause saves a resume checkpoint; paused status and history switch back to Chinese")
            app.count_box.current(2)
            app.start_button.invoke()
            wait(lambda:not app.busy)
            assert output.read_bytes()==server.payload
            assert app.progress["value"]==100
            report=json.loads(Path(str(output)+".report.json").read_text())
            assert report["resumed_bytes"]>0
            screenshot("chinese_complete.png")
            language("English")
            assert app.status.get().startswith("Complete. SHA-256:")
            assert report["sha256"] in app.status.get()
            assert "Total" in app.summary.get()
            screenshot("english_complete.png")
            checks.append("Resume under Chinese with two adapters completes byte-for-byte; completion, progress and SHA stay correct in English")
            app.url.set(server.url("rate"))
            app.output.set(str(Path(temp)/"rate.bin"))
            app.start_button.invoke()
            wait(lambda:not app.busy)
            assert "HTTP 429" in app.status.get() and "entire task" in app.status.get()
            assert not re.search(r"[\u4e00-\u9fff]",app.status.get())
            language("简体中文")
            assert "HTTP 429" in app.status.get() and "停止" in app.status.get()
            checks.append("Real HTTP 429 error is displayed in English and retranslated to Chinese")
            # 非 ASCII 网卡名称不能被翻译或丢失。
            app.cards[1].name="中文网卡"
            app.events.put(("selected",[app.cards[1].info()]))
            wait(lambda:"中文网卡" in app.status.get())
            language("English")
            assert app.status.get()=="Download adapters: 中文网卡"
            checks.append("User-provided adapter names remain unchanged")
            app.url.set(server.url())
            app.output.set(str(Path(temp)/"close.bin"))
            app.count_box.current(1)
            app.start_button.invoke()
            wait(lambda:sum(app.job.bytes_by_link.values())>=MIB)
            app.close()
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                root.update()
                if not app.busy:
                    break
                time.sleep(.02)
            assert not app.busy and Path(temp,"close.bin.multilink.json").exists()
            assert not Path(temp,"close.bin.lock").exists()
            checks.append("Closing during an English download waits for resume save and lock release")
            # 真正重新创建窗口，确认读取上次选择；不触碰用户配置。
            root2=tk.Tk()
            restarted=gui.App(root2,settings)
            root2.update_idletasks()
            assert restarted.language=="en" and restarted.start_button["text"]=="Start / Resume"
            restarted.close()
            checks.append("A newly created window restores the saved English preference")
            assert not errors,errors
        finally:
            server.close()
            try:
                app.destroy()
            except tk.TclError:
                pass
    result=dict(passed=True,checks=checks,callback_errors=errors,seconds=round(time.monotonic()-started,3),
                platform=sys.platform,scope="Real Tk + real HTTP; only adapter discovery is simulated")
    Path(__file__).with_name("gui_results.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
