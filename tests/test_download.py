"""云端真实 HTTP/套接字测试；只替换网卡发现，不替换下载器或文件写入。"""
import hashlib
import http.server
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from downloader import Cancelled, Downloader, FatalError, MIB, RateLimited
from network import Interface, parse_interfaces


class Pacer:
    """按源 IP 合并该链路所有连接的带宽，避免把连接数当链路数。"""
    def __init__(self, rate):
        self.rate, self.next, self.lock = rate * MIB, 0, threading.Lock()

    def wait(self, count):
        with self.lock:
            self.next = max(time.monotonic(), self.next) + count / self.rate
            deadline = self.next
        time.sleep(max(0, deadline - time.monotonic()))


class Server(http.server.ThreadingHTTPServer):
    """可控 Range 服务，记录每张网卡真正到达服务端的源 IP。"""
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, rates=(3, 2, 1.3, 0.8), size=6 * MIB):
        super().__init__(("127.0.0.1", 0), Handler)
        self.payload = (bytes(range(251)) * (size // 251 + 1))[:size]
        self.etag = '"' + hashlib.sha256(self.payload).hexdigest() + '"'
        self.pacers = {f"127.0.0.{i+2}": Pacer(rate) for i, rate in enumerate(rates)}
        self.shared, self.active = False, 0
        self.lock = threading.Lock()
        self.sources, self.records, self.cut = set(), [], set()
        self.stage, self.offline, self.limit = "prepare", set(), False
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def handle_error(self, request, address):
        """测试主动关闭测速连接会产生 BrokenPipe，不打印预期噪声。"""
        pass

    def url(self, route="file"):
        return f"http://127.0.0.1:{self.server_port}/{route}"

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join()


class Handler(http.server.BaseHTTPRequestHandler):
    """实现标准 Range、重定向和可控错误注入。"""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, status, **headers):
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), str(value))
        self.end_headers()

    def do_GET(self):
        server, ip = self.server, self.client_address[0]
        with server.lock:
            server.sources.add(ip)
            server.records.append((server.stage, ip, self.path, self.headers.get("Range")))
        if self.path == "/redirect":
            self.reply(302, Location="/file", Content_Length=0)
            return
        if self.path == "/rate" or server.limit:
            self.reply(429, Retry_After=15, Content_Length=0)
            return
        if ip in server.offline:
            self.reply(503, Content_Length=0)
            return
        if self.path == "/no_range":
            self.reply(200, Content_Length=0)
            return
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
        start, end = map(int, match.groups())
        if end >= len(server.payload):
            self.reply(416, Content_Length=0)
            return
        headers = dict(Content_Range=f"bytes {start}-{end}/{len(server.payload)}", Content_Length=end - start + 1)
        if self.path != "/no_validator":
            headers["ETag"] = server.etag
        if self.path == "/wrong":
            headers["Content_Range"] = f"bytes {start+1}-{end+1}/{len(server.payload)}"
        self.reply(206, **headers)
        with server.lock:
            server.active += 1
        try:
            # 下载中第一次大块故意断开，校验器必须丢弃半块并重试。
            cut = self.path == "/cut" and server.stage == "download" and ip not in server.cut
            if cut:
                server.cut.add(ip)
            limit = min(end + 1, start + 8192) if cut else end + 1
            for pos in range(start, limit, 8192):
                data = server.payload[pos:min(limit, pos + 8192)]
                if server.shared:
                    with server.lock:
                        active = server.active
                    # 模拟多连接争用同一出口后出现额外拥塞，单路应胜出。
                    time.sleep(len(data) * active * active / (4 * MIB))
                else:
                    server.pacers[ip].wait(len(data))
                self.wfile.write(data)
                self.wfile.flush()
            if cut:
                self.close_connection = True
        finally:
            with server.lock:
                server.active -= 1


class DownloadTests(unittest.TestCase):
    """所有完整文件测试都独立比对源数据，而非只检查程序退出码。"""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = Server()
        self.cards = [Interface(i+1, f"127.0.0.{i+2}", f"模拟网卡{i+1}") for i in range(4)]

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def job(self, name="test.bin", route="file", count=0, cards=None, extra=None, stop=None):
        def event(kind, data):
            if kind == "selected":
                self.server.stage = "download"
            if extra:
                extra(kind, data)
        return Downloader(self.server.url(route), self.root / name,
                          cards if cards is not None else self.cards, count, event, stop,
                          sample_seconds=0.16)

    def verify(self, report, name="test.bin"):
        self.assertEqual((self.root / name).read_bytes(), self.server.payload)
        self.assertEqual(report["sha256"], hashlib.sha256(self.server.payload).hexdigest())
        self.assertFalse((self.root / (name + ".multilink.json")).exists())
        self.assertFalse((self.root / (name + ".lock")).exists())

    def test_01_interface_filter(self):
        rows = [dict(Index=1, IP="192.168.1.2", Name="WiFi", Status="Up"),
                dict(Index=2, IP="127.0.0.1"), dict(Index=3, IP="169.254.1.2"),
                dict(Index=4, IP="10.0.0.2", Status="Down"), dict(Index=1, IP="192.168.1.3")]
        self.assertEqual([it.index for it in parse_interfaces(rows)], [1])
        self.assertEqual(len(parse_interfaces(rows[0])), 1)

    def test_02_counts_one_to_four(self):
        for count in (1, 2, 3, 4):
            with self.subTest(count=count):
                report = self.job(name=f"{count}.bin", count=count).run()
                self.verify(report, f"{count}.bin")
                self.assertEqual(len(report["selected"]), count)
                self.assertTrue(all(n > 0 for n in report["downloaded_per_link"].values()))
        self.assertEqual(len(self.server.sources), 4)

    def test_03_auto_independent_four(self):
        report = self.job().run()
        self.verify(report)
        self.assertEqual(len(report["benchmarks"]), 15)
        self.assertEqual(len(report["selected"]), 4)

    def test_04_auto_shared_single(self):
        self.server.shared = True
        report = self.job().run()
        self.verify(report)
        self.assertEqual(len(report["selected"]), 1)

    def test_05_redirect(self):
        report = self.job(route="redirect", cards=self.cards[:1]).run()
        self.verify(report)

    def test_06_reject_no_range(self):
        with self.assertRaises(RuntimeError):
            self.job(route="no_range").run()
        self.assertFalse((self.root / "test.bin").exists())

    def test_07_reject_bad_content_range(self):
        with self.assertRaises(RuntimeError):
            self.job(route="wrong").run()
        self.assertFalse((self.root / "test.bin").exists())

    def test_08_429_probe_global_stop(self):
        with self.assertRaises(RateLimited):
            self.job(route="rate").run()
        self.assertFalse((self.root / "test.bin").exists())
        self.assertLessEqual(len(self.server.records), 4)

    def test_09_429_download_checkpoint(self):
        def event(kind, data):
            if kind == "progress" and data["done"] >= MIB:
                self.server.limit = True
        with self.assertRaises(RateLimited):
            self.job(count=4, extra=event).run()
        self.assertTrue((self.root / "test.bin.multilink.json").exists())
        self.assertFalse((self.root / "test.bin").exists())

    def paused(self, route="file"):
        stop = threading.Event()
        def event(kind, data):
            if kind == "progress" and data["done"] >= MIB:
                stop.set()
        with self.assertRaises(Cancelled):
            self.job(route=route, count=1, cards=self.cards[:1], extra=event, stop=stop).run()
        return json.loads((self.root / "test.bin.multilink.json").read_text())

    def test_10_pause_resume_corrupt_block(self):
        state = self.paused()
        index = int(next(iter(state["done"])))
        with (self.root / "test.bin.part").open("r+b") as f:
            f.seek(index * state["chunk_bytes"])
            f.write(b"CORRUPTED")
        report = self.job(count=1, cards=self.cards[:1]).run()
        self.verify(report)
        self.assertGreater(report["resumed_bytes"], 0)
        self.assertLess(report["resumed_bytes"], len(state["done"]) * state["chunk_bytes"])

    def test_11_resume_version_changed(self):
        self.paused()
        self.server.etag = '"new-version"'
        with self.assertRaises(FatalError):
            self.job(count=1, cards=self.cards[:1]).run()
        self.assertFalse((self.root / "test.bin").exists())

    def test_12_missing_validator_resume_refused(self):
        self.paused(route="no_validator")
        with self.assertRaises(FatalError):
            self.job(route="no_validator", cards=self.cards[:1]).run()

    def test_13_hash_mismatch_preserves_part(self):
        job = self.job(cards=self.cards[:1])
        job.expected = "0" * 64
        with self.assertRaises(FatalError):
            job.run()
        self.assertTrue(job.part.exists())
        self.assertFalse(job.output.exists())

    def test_14_no_overwrite_or_concurrent_run(self):
        path = self.root / "test.bin"
        path.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            self.job().run()
        self.assertEqual(path.read_bytes(), b"existing")
        path.unlink()
        (self.root / "test.bin.lock").write_text("locked")
        with self.assertRaises(RuntimeError):
            self.job().run()

    def test_15_broken_response_retry(self):
        report = self.job(route="cut", count=2, cards=self.cards[:2]).run()
        self.verify(report)
        self.assertGreaterEqual(report["retries"], 2)

    def test_16_link_failure_fallback(self):
        def event(kind, data):
            if kind == "selected":
                self.server.offline.add("127.0.0.5")
        report = self.job(count=4, extra=event).run()
        self.verify(report)
        self.assertIn(4, report["disabled_indices"])

    def test_17_slow_tail_takeover(self):
        def event(kind, data):
            if kind == "selected":
                self.server.pacers["127.0.0.5"].rate = 0.025 * MIB
        report = self.job(count=4, extra=event).run()
        self.verify(report)
        self.assertGreater(report["tail_takeovers"], 0)

    def test_18_insufficient_cards(self):
        with self.assertRaises(ValueError):
            self.job(count=4, cards=self.cards[:2]).run()

    def test_19_remote_version_changes_mid_download(self):
        def event(kind, data):
            if kind == "selected":
                self.server.etag = '"changed-after-probe"'
        with self.assertRaises(FatalError):
            self.job(count=2, cards=self.cards[:2], extra=event).run()
        self.assertFalse((self.root / "test.bin").exists())

    def test_20_pause_during_prepare(self):
        stop = threading.Event()
        def event(kind, data):
            if kind == "interface":
                stop.set()
        with self.assertRaises(Cancelled):
            self.job(extra=event, stop=stop).run()
        self.assertFalse((self.root / "test.bin").exists())
        self.assertFalse((self.root / "test.bin.lock").exists())


    def test_21_small_and_unaligned_files(self):
        for name, payload in (("tiny.bin", b"x"), ("tail.bin", self.server.payload[:MIB] + b"uneven-tail")):
            self.server.payload = payload
            self.server.etag = '"' + hashlib.sha256(payload).hexdigest() + '"'
            report = self.job(name=name, count=2, cards=self.cards[:2]).run()
            self.verify(report, name)
        self.assertLess(len(self.server.records), 80)  # 小文件不会被循环请求测速。

    def test_22_more_than_four_candidates(self):
        cards = self.cards + [Interface(5, "127.0.0.6", "模拟网卡5")]
        self.server.pacers["127.0.0.6"] = Pacer(0.3)
        report = self.job(cards=cards).run()
        self.verify(report)
        self.assertLessEqual(len(report["selected"]), 4)
        self.assertEqual(len(report["benchmarks"]), 16)
        self.assertNotIn(5, [it["index"] for it in report["selected"]])

    def test_23_no_validator_fresh_download(self):
        report = self.job(route="no_validator", cards=self.cards[:2]).run()
        self.verify(report)

    def test_24_matching_expected_hash(self):
        job = self.job(cards=self.cards[:1])
        job.expected = hashlib.sha256(self.server.payload).hexdigest()
        report = job.run()
        self.verify(report)
        self.assertTrue(report["verified_expected_sha256"])


class RecordedResult(unittest.TextTestResult):
    """记录真实执行时间，供随包测试报告引用。"""
    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def addSuccess(self, test):
        RECORDS.append(dict(test=test.id(), status="PASS", seconds=round(time.monotonic()-self.started, 3)))
        super().addSuccess(test)

    def addFailure(self, test, err):
        RECORDS.append(dict(test=test.id(), status="FAIL", error=str(err[1])))
        super().addFailure(test, err)

    def addError(self, test, err):
        RECORDS.append(dict(test=test.id(), status="ERROR", error=str(err[1])))
        super().addError(test, err)


RECORDS = []
if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DownloadTests)
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(suite)
    target = Path(__file__).resolve().parent / "cloud_results.json"
    target.write_text(json.dumps(dict(passed=result.wasSuccessful(), count=result.testsRun,
                                      platform=sys.platform, tests=RECORDS), ensure_ascii=False, indent=2), encoding="utf-8")
    sys.exit(0 if result.wasSuccessful() else 1)
