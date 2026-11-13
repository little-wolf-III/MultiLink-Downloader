"""最多四路下载核心：测速选路、动态分块、慢路收尾、校验和安全续传。"""
import argparse
import hashlib
import http.client
import itertools
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from network import Cancelled, FatalError, RateLimited, Session, interfaces, validate_url

MIB = 1024 * 1024


def digest(data):
    """输入字节，输出 SHA-256 十六进制值。"""
    return hashlib.sha256(data).hexdigest()


def atomic_json(path, data):
    """写入临时文件并原子替换；返回前刷盘。"""
    temp = Path(str(path) + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def hash_file(path, stop=None):
    """分块计算完整文件哈希；支持用户暂停。"""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            if stop and stop():
                raise Cancelled()
            block = f.read(MIB)
            if not block:
                return h.hexdigest()
            h.update(block)


class Downloader:
    """输入地址、输出路径及候选网卡；run 返回报告，通过 emit 回传界面事件。"""
    def __init__(self, url, output, candidates=None, count=0, emit=None, stop=None,
                 expected_sha256="", sample_seconds=0.7, connections=2, chunk_kb=128):
        self.url, self.output = validate_url(url.strip()), Path(output).expanduser().resolve()
        if count not in range(5):
            raise ValueError("链路数量只能是自动（0）或 1～4。")
        if connections not in (1, 2, 3, 4) or chunk_kb not in (64, 128, 256, 512):
            raise ValueError("每路连接数为 1～4，分块大小为 64/128/256/512 KiB。")
        self.expected = expected_sha256.strip().lower()
        if self.expected and (len(self.expected) != 64 or any(c not in "0123456789abcdef" for c in self.expected)):
            raise ValueError("预期 SHA-256 应为 64 位十六进制字符。")
        self.candidates, self.count = candidates, count
        self.emit = emit or (lambda kind, data: None)
        self.stop = stop if stop is not None else threading.Event()
        self.abort, self.lock = threading.Event(), threading.RLock()
        self.sample_seconds, self.connections, self.unit = sample_seconds, connections, chunk_kb * 1024
        self.part = Path(str(self.output) + ".part")
        self.state_path = Path(str(self.output) + ".multilink.json")
        self.lock_path = Path(str(self.output) + ".lock")
        self.report_path = Path(str(self.output) + ".report.json")
        self.done, self.inflight, self.attempts = {}, {}, {}
        self.error, self.file = None, None
        self.benchmarks, self.excluded = [], []
        self.bytes_by_link, self.speeds, self.disabled = {}, {}, set()
        self.hedges, self.retries, self.resumed = 0, 0, 0

    def stopped(self):
        """返回用户暂停或内部终止状态。"""
        return self.stop.is_set() or self.abort.is_set()

    def log(self, text):
        """向调用方输出一行日志。"""
        self.emit("log", text)

    def fail(self, error):
        """记录第一个全局错误，通知所有线程终止。"""
        with self.lock:
            if self.error is None:
                self.error = error
            self.abort.set()

    def probe(self, iface):
        """验证 Range、版本及头中尾样本；返回元数据和样本哈希。"""
        session = Session(self.url, iface)
        try:
            _, meta = session.fetch(0, 0, stop=self.stopped)
            size = meta["size"]
            samples = []
            for start in sorted({0, max(0, size // 2 - 128), max(0, size - 256)}):
                data, _ = session.fetch(start, min(size - 1, start + 255), meta, self.stopped)
                samples.append(data)
            meta["signature"] = digest(b"".join(samples))
            return meta
        except RateLimited as exc:
            self.fail(exc)
            raise
        finally:
            session.close()

    def measure(self, group):
        """实际并发访问目标文件，返回组合有效吞吐；每路使用与下载相同的连接数。"""
        barrier = threading.Barrier(len(group) * self.connections)
        def sample(iface):
            session = Session(self.url, iface)
            try:
                barrier.wait(timeout=10)
                # 每个线程最多读取 512 KiB；四张候选的全部组合共不超过约 32 MiB。
                # 每次只发一个请求，小文件不会在测速期被反复请求。
                data, _ = session.fetch(0, min(self.meta["size"] - 1, MIB // 2 - 1),
                                        self.meta, self.stopped, self.sample_seconds)
                return iface.index, len(data)
            except RateLimited as exc:
                self.fail(exc)
                raise
            finally:
                session.close()
        started = time.monotonic()
        rates = {it.index: 0 for it in group}
        with ThreadPoolExecutor(max_workers=len(group) * self.connections) as pool:
            futures = [pool.submit(sample, it) for it in group for _ in range(self.connections)]
            for f in as_completed(futures):
                index, size = f.result()
                rates[index] += size
        elapsed = max(time.monotonic() - started, 0.001)
        rates = {index: size / elapsed for index, size in rates.items()}
        entry = dict(indices=[it.index for it in group], bytes_per_second=sum(rates.values()),
                     per_link=rates, seconds=elapsed)
        self.benchmarks.append(entry)
        self.log("测速 " + "+".join(it.name for it in group) + f"：{entry['bytes_per_second']/MIB:.2f} MiB/s")
        return entry

    def prepare(self):
        """发现候选、验证文件一致性，并在最快四张候选中比较组合。"""
        candidates = self.candidates if self.candidates is not None else interfaces()
        candidates = list({it.index: it for it in candidates}.values())
        if not candidates:
            raise ValueError("没有可用候选网卡。请连接网络，刷新网卡并勾选。")
        self.log(f"正在探测 {len(candidates)} 张候选网卡……")
        found = {}
        with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as pool:
            futures = {pool.submit(self.probe, it): it for it in candidates}
            for f in as_completed(futures):
                it = futures[f]
                try:
                    found[it.index] = f.result()
                    self.emit("interface", dict(index=it.index, status="目标可达"))
                except (RateLimited, Cancelled):
                    raise
                except Exception as exc:
                    self.excluded.append(dict(index=it.index, reason=str(exc)))
                    self.emit("interface", dict(index=it.index, status="不可用：" + str(exc)))
        if self.error:
            raise self.error
        if self.stopped():
            raise Cancelled()
        good = [it for it in candidates if it.index in found]
        if not good:
            raise RuntimeError("所有候选网卡均无法下载目标。请查看网卡状态和直链是否有效。")
        self.meta = found[good[0].index]
        # 跨网卡的版本、大小和样本必须一致，不能拼接不同内容。
        for it in good:
            if found[it.index] != self.meta:
                raise FatalError(f"{it.name} 返回的文件与其他线路不同，拒绝拼接。")
        singles = []
        for it in good:
            try:
                result = self.measure([it])
                singles.append((it, result))
                self.emit("interface", dict(index=it.index, status="可用", speed=result["bytes_per_second"]))
            except (FatalError, Cancelled):
                raise
            except Exception as exc:
                self.excluded.append(dict(index=it.index, reason=str(exc)))
                self.emit("interface", dict(index=it.index, status="测速失败：" + str(exc)))
        singles.sort(key=lambda pair: pair[1]["bytes_per_second"], reverse=True)
        top = [it for it, result in singles[:4]]
        if len(top) < (self.count or 1):
            raise ValueError(f"指定 {self.count or 1} 条链路，但只有 {len(top)} 条通过探测和测速。")
        if len(singles) > 4:
            self.log("候选超过四张：仅在单路实测最快的四张中比较组合。")
        choices = [result for it, result in singles[:4]] if self.count in (0, 1) else []
        sizes = range(2, len(top) + 1) if not self.count else ([self.count] if self.count > 1 else [])
        for size in sizes:
            for group in itertools.combinations(top, size):
                if self.stopped():
                    raise Cancelled()
                try:
                    choices.append(self.measure(group))
                except (FatalError, Cancelled):
                    raise
                except Exception as exc:
                    self.log("组合测速失败，跳过：" + str(exc))
        if not choices:
            raise RuntimeError("没有成功完成测速的组合。")
        best = max(choices, key=lambda result: result["bytes_per_second"])
        self.selected = [it for it in top if it.index in best["indices"]]
        self.speeds = best["per_link"].copy()
        self.bytes_by_link = {it.index: 0 for it in self.selected}
        self.log("已选择：" + " + ".join(it.name for it in self.selected))
        self.emit("selected", [it.info() for it in self.selected])

    def load_state(self):
        """加载 v2.1 断点；验证版本及已落盘块的哈希，损坏块重新下载。"""
        self.total = (self.meta["size"] + self.unit - 1) // self.unit
        self.identity = dict(version="2.1", url_hash=digest(self.url.encode()), meta=self.meta, chunk_bytes=self.unit)
        if self.part.exists() != self.state_path.exists():
            raise ValueError("断点文件不完整：.part 与 .multilink.json 必须成对保留；请换保存名。")
        if not self.part.exists():
            self.file = self.part.open("x+b")
            self.file.truncate(self.meta["size"])
            self.checkpoint()
            return
        if not self.meta["validator"]:
            raise FatalError("目标没有可靠版本标识，不能安全跨运行续传；请换保存名重新下载。")
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if any(state.get(k) != v for k, v in self.identity.items()):
            raise FatalError("断点的地址、文件版本或分块大小不一致；请换保存名。")
        if self.part.stat().st_size != self.meta["size"]:
            raise FatalError("断点文件大小异常；请换保存名。")
        self.file = self.part.open("r+b")
        for key, sha in state.get("done", {}).items():
            if self.stopped():
                raise Cancelled()
            index = int(key)
            if index < 0 or index >= self.total:
                raise FatalError("断点记录中的块编号异常。")
            self.file.seek(index * self.unit)
            if digest(self.file.read(self.block_size(index))) == sha:
                self.done[index] = sha
        self.resumed = sum(self.block_size(i) for i in self.done)
        self.log(f"续传校验完成：保留 {self.resumed/MIB:.2f} MiB，损坏块自动重下。")

    def block_size(self, index):
        """返回给定块的字节数，最后一块可能较短。"""
        return min(self.unit, self.meta["size"] - index * self.unit)

    def checkpoint(self):
        """先刷数据再保存已完成块，避免断点宣称尚未落盘的数据已经完成。"""
        with self.lock:
            if self.file:
                self.file.flush()
                os.fsync(self.file.fileno())
                atomic_json(self.state_path, dict(self.identity, done=self.done))

    def claim(self, iface, token):
        """分配尚未下载的块；收尾时允许最快线路接替慢路未完成块。"""
        with self.lock:
            if iface.index in self.disabled or self.stopped():
                return None
            index = next((i for i in self.pending if i not in self.done and i not in self.inflight), None)
            now = time.monotonic()
            if index is None:
                available = [it for it in self.selected if it.index not in self.disabled]
                fastest = max(available, key=lambda it: self.speeds[it.index]).index
                if iface.index == fastest:
                    for i, owners in self.inflight.items():
                        if i in self.done or len(owners) != 1:
                            continue
                        owner, started = next(iter(owners.values()))
                        threshold = max(0.25, 2 * self.block_size(i) / max(self.speeds[fastest], 1))
                        if owner != fastest and now - started > threshold:
                            index = i
                            self.hedges += 1
                            break
            if index is not None:
                self.inflight.setdefault(index, {})[token] = (iface.index, now)
            return index

    def worker(self, iface, number):
        """下载线程：验证整块后写入；临时失败换连接，持续失败隔离该网卡。"""
        session, token = Session(self.url, iface), (iface.index, number)
        failures = 0
        try:
            while not self.stopped():
                index = self.claim(iface, token)
                if index is None:
                    with self.lock:
                        if len(self.done) == self.total or iface.index in self.disabled:
                            return
                    self.stop.wait(0.05)
                    continue
                try:
                    started = time.monotonic()
                    def obsolete():
                        with self.lock:
                            return self.stopped() or index in self.done
                    data, _ = session.fetch(index * self.unit, index * self.unit + self.block_size(index) - 1,
                                            self.meta, obsolete)
                    elapsed = max(time.monotonic() - started, 0.001)
                    with self.lock:
                        if index not in self.done:
                            try:
                                self.file.seek(index * self.unit)
                                self.file.write(data)
                            except OSError as exc:
                                raise FatalError("文件写入失败，请检查磁盘空间和权限：" + str(exc)) from exc
                            self.done[index] = digest(data)
                            self.pending.discard(index)
                            self.bytes_by_link[iface.index] += len(data)
                            self.speeds[iface.index] = 0.75 * self.speeds[iface.index] + 0.25 * len(data) / elapsed * self.connections
                    failures = 0
                except Cancelled:
                    pass
                except FatalError as exc:
                    self.fail(exc)
                    return
                except (OSError, http.client.HTTPException) as exc:
                    session.close()
                    with self.lock:
                        failures += 1
                        self.retries += 1
                        self.attempts[index] = self.attempts.get(index, 0) + 1
                        if failures >= 3:
                            self.disabled.add(iface.index)
                            self.log(f"{iface.name} 连续失败，已停用：{exc}")
                        if len(self.disabled) == len(self.selected) or self.attempts[index] >= 12:
                            self.fail(RuntimeError("链路持续失败，已保存断点：" + str(exc)))
                    self.stop.wait(0.15)
                except Exception as exc:
                    self.fail(exc)
                    return
                finally:
                    with self.lock:
                        owners = self.inflight.get(index, {})
                        owners.pop(token, None)
                        if not owners:
                            self.inflight.pop(index, None)
        finally:
            session.close()

    def transfer(self):
        """启动最多四路并发，定期发进度与断点，等待线程安全退出。"""
        self.pending = set(range(self.total)) - set(self.done)
        started, last_save, previous = time.monotonic(), time.monotonic(), 0
        last_progress = started
        with ThreadPoolExecutor(max_workers=len(self.selected) * self.connections) as pool:
            futures = [pool.submit(self.worker, it, n) for it in self.selected for n in range(self.connections)]
            while not all(f.done() for f in futures):
                with self.lock:
                    copied = sum(self.bytes_by_link.values())
                    now = time.monotonic()
                    self.emit("progress", dict(done=self.resumed + copied, total=self.meta["size"],
                              speed=(copied - previous) / max(0.001, now - last_progress), per_link=self.bytes_by_link.copy()))
                    previous, last_progress = copied, now
                if time.monotonic() - last_save >= 1:
                    self.checkpoint()
                    last_save = time.monotonic()
                # 即使正在停止也等待短间隔，避免忙循环占满 CPU。
                time.sleep(0.25)
            for f in futures:
                f.result()
        self.checkpoint()
        if self.error:
            raise self.error
        if self.stopped():
            raise Cancelled()
        if len(self.done) != self.total:
            raise RuntimeError("仍有未完成分块，断点已保存。")
        return time.monotonic() - started

    def run(self):
        """运行完整流程；成功返回报告，暂停抛 Cancelled，所有退出路径释放文件。"""
        start = time.monotonic()
        self.output.parent.mkdir(parents=True, exist_ok=True)
        if self.output.exists():
            raise FileExistsError("目标文件已存在，请更换保存文件名。")
        try:
            lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError("同名任务正在运行或上次异常退出留下 .lock；确认无任务运行后删除该锁文件。") from None
        os.close(lock_fd)
        try:
            self.prepare()
            self.load_state()
            self.log("开始下载；每路 " + str(self.connections) + " 个连接。")
            download_seconds = self.transfer()
            self.file.close()
            self.file = None
            self.log("下载完成，正在校验完整 SHA-256……")
            sha = hash_file(self.part, self.stopped)
            if self.expected and sha != self.expected:
                raise FatalError("SHA-256 与预期不符，保留 .part，未发布最终文件。")
            # Windows rename 不覆盖已有文件；POSIX 用硬链接获得相同的不覆盖保证。
            if os.name == "nt":
                os.rename(self.part, self.output)
            else:
                os.link(self.part, self.output)
                self.part.unlink()
            report = dict(version="2.1", size=self.meta["size"], sha256=sha,
                          verified_expected_sha256=bool(self.expected), resumed_bytes=self.resumed,
                          download_seconds=download_seconds, total_seconds=time.monotonic() - start,
                          selected=[it.info() for it in self.selected], downloaded_per_link=self.bytes_by_link,
                          disabled_indices=sorted(self.disabled), retries=self.retries, tail_takeovers=self.hedges,
                          benchmarks=self.benchmarks, excluded=self.excluded)
            atomic_json(self.report_path, report)
            self.state_path.unlink(missing_ok=True)
            self.emit("progress", dict(done=self.meta["size"], total=self.meta["size"], speed=0,
                                       per_link=self.bytes_by_link.copy()))
            self.emit("done", report)
            return report
        except Cancelled:
            if self.error:
                raise self.error
            raise
        finally:
            try:
                if self.file:
                    try:
                        self.checkpoint()
                    finally:
                        self.file.close()
                        self.file = None
            finally:
                self.lock_path.unlink(missing_ok=True)


def main():
    """可选命令行入口；默认仍推荐双击界面启动器。"""
    parser = argparse.ArgumentParser(description="多链路下载器 v2.1")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--out")
    parser.add_argument("--interfaces", help="候选网卡编号，用逗号分隔；省略时自动发现")
    parser.add_argument("--count", type=int, default=0, help="0=自动，1～4=指定数量")
    parser.add_argument("--sha256", default="")
    args = parser.parse_args()
    found = interfaces()
    if args.list:
        for it in found:
            print(it.index, it.name, it.ip, it.gateway)
        return
    if not args.url or not args.out:
        parser.error("下载需要 --url 和 --out")
    if args.interfaces:
        chosen = set(map(int, args.interfaces.split(",")))
        if chosen - {it.index for it in found}:
            parser.error("指定网卡不存在，请先 --list")
        found = [it for it in found if it.index in chosen]
    stop = threading.Event()
    import signal
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    def emit(kind, data):
        if kind == "log":
            print(data, flush=True)
    try:
        report = Downloader(args.url, args.out, found, args.count, emit, stop, args.sha256).run()
        print("完成，SHA-256：", report["sha256"])
    except Cancelled:
        print("已暂停。使用相同地址和保存路径继续。")


if __name__ == "__main__":
    main()
