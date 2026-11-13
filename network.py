"""网卡发现和绑定 HTTP：仅使用标准库，Windows 10/11 + Python 3.10+。"""
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import struct
import subprocess
import time
from urllib.parse import urljoin, urlsplit


class Interface:
    """一条 IPv4 链路；输入编号、地址、名称、网关。"""
    def __init__(self, index, ip, name, gateway=""):
        self.index, self.ip, self.name, self.gateway = int(index), ip, name, gateway

    def info(self):
        """输出可写入报告的网卡信息。"""
        return dict(index=self.index, ip=self.ip, name=self.name, gateway=self.gateway)


def parse_interfaces(rows):
    """将 PowerShell JSON 转成网卡列表；排除断开、回环、自分配和重复接口。"""
    result, seen = [], set()
    for row in rows if isinstance(rows, list) else [rows]:
        ip = row.get("IP", "")
        try:
            addr = ipaddress.IPv4Address(ip)
            index = int(row["Index"])
        except (ValueError, KeyError, TypeError):
            continue
        if addr.is_loopback or addr.is_link_local or addr.is_unspecified:
            continue
        if row.get("Status", "Up") != "Up" or index in seen:
            continue
        seen.add(index)
        result.append(Interface(index, ip, row.get("Name", str(index)), row.get("Gateway") or ""))
    return sorted(result, key=lambda it: it.index)


def interfaces():
    """读取 Windows 已连接的 IPv4 网卡；是否能访问目标由后续探测判断。"""
    if os.name != "nt":
        raise RuntimeError("自动识别网卡需要 Windows 10/11；云端测试使用模拟接口。")
    script = r'''$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$x=@(Get-NetIPConfiguration -All | Where-Object {
 $_.IPv4Address -and $_.NetAdapter.Status -eq 'Up'
} | ForEach-Object {
 [PSCustomObject]@{Index=[int]$_.InterfaceIndex; Name=[string]$_.InterfaceAlias;
 IP=[string](@($_.IPv4Address.IPAddress)[0]);
 Gateway=[string](@($_.IPv4DefaultGateway.NextHop)[0]); Status=[string]$_.NetAdapter.Status}
})
ConvertTo-Json -InputObject $x -Compress -Depth 3'''
    p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, timeout=25, creationflags=0x08000000)
    if p.returncode:
        raise RuntimeError(p.stderr.decode("utf-8", "replace"))
    return parse_interfaces(json.loads(p.stdout.decode("utf-8-sig", "replace") or "[]"))


class FatalError(Exception):
    """不可通过重试或换线路修复的错误。"""


class RateLimited(FatalError):
    """目标返回 429，停止整个任务并保留断点。"""


class Cancelled(Exception):
    """用户暂停、任务停止或此分块已由另一线程完成。"""


class BoundHTTP(http.client.HTTPConnection):
    """HTTP 连接绑定源 IPv4；Windows 同时指定出站接口。"""
    def __init__(self, host, port, iface, timeout):
        super().__init__(host, port, timeout=timeout)
        self.iface = iface

    def connect(self):
        """创建绑定套接字；连接失败会关闭套接字并尝试下一个 IPv4。"""
        last = None
        for family, kind, proto, _, addr in socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM):
            sock = socket.socket(family, kind, proto)
            try:
                sock.settimeout(self.timeout)
                if os.name == "nt":
                    sock.setsockopt(socket.IPPROTO_IP, 31, struct.pack("!I", self.iface.index))
                sock.bind((self.iface.ip, 0))
                sock.connect(addr)
                self.sock = sock
                return
            except OSError as exc:
                last = exc
                sock.close()
        raise OSError(f"{self.iface.name} 连接失败：{last}")


class BoundHTTPS(BoundHTTP):
    """绑定链路的 HTTPS，启用系统证书及主机名校验。"""
    def connect(self):
        super().connect()
        try:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)
        except Exception:
            self.close()
            raise


def validate_url(url):
    """检查下载地址；输入 URL，错误时抛异常。"""
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        raise ValueError("请输入 HTTP/HTTPS 文件直链，不支持带用户名密码的 URL。")
    return url


class Session:
    """每个线程独立复用连接，避免跨线程共享 HTTPConnection。"""
    def __init__(self, url, iface, timeout=6):
        self.url, self.iface, self.timeout = validate_url(url), iface, timeout
        self.conn, self.key = None, None

    def close(self):
        """释放当前连接；可重复调用。"""
        if self.conn:
            self.conn.close()
        self.conn, self.key = None, None

    def response(self, start, end, validator=""):
        """发起 Range 请求；处理跳转，遇到 429 立即抛全局停止异常。"""
        for _ in range(6):
            u = urlsplit(validate_url(self.url))
            key = (u.scheme, u.hostname, u.port or (443 if u.scheme == "https" else 80))
            if self.key != key or self.conn is None:
                self.close()
                cls = BoundHTTPS if u.scheme == "https" else BoundHTTP
                self.conn, self.key = cls(key[1], key[2], self.iface, self.timeout), key
            headers = {"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity",
                       "User-Agent": "MultiLinkDownloader/2.1"}
            if validator:
                headers["If-Range"] = validator
            self.conn.request("GET", (u.path or "/") + ("?" + u.query if u.query else ""), headers=headers)
            response = self.conn.getresponse()
            if response.status == 429:
                retry = response.getheader("Retry-After", "未提供")
                self.close()
                raise RateLimited(f"HTTP 429：已停止整个任务，Retry-After={retry}；稍后手动续传。")
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                self.close()
                if not location:
                    raise FatalError("跳转缺少 Location。")
                target = validate_url(urljoin(self.url, location))
                if u.scheme == "https" and urlsplit(target).scheme != "https":
                    raise FatalError("拒绝 HTTPS 降级到 HTTP。")
                self.url = target
                continue
            return response
        raise FatalError("重定向次数过多。")

    def fetch(self, start, end, meta=None, stop=None, seconds=0):
        """读取并验证一段数据；seconds>0 仅用于限时测速，允许主动截断。"""
        try:
            if stop and stop():
                raise Cancelled()
            r = self.response(start, end, meta["validator"] if meta else "")
            if r.status != 206:
                error = FatalError if r.status < 500 else OSError
                raise error(f"HTTP {r.status}，目标必须支持 Range 并返回 206。")
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", r.getheader("Content-Range", ""))
            if not match or tuple(map(int, match.groups()[:2])) != (start, end):
                raise FatalError("Content-Range 与请求不一致，拒绝拼接。")
            total = int(match.group(3))
            if total <= end or r.getheader("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise FatalError("文件范围或编码异常。")
            length = r.getheader("Content-Length")
            if length is not None and int(length) != end - start + 1:
                raise FatalError("Content-Length 与请求不一致。")
            etag, modified = r.getheader("ETag", ""), r.getheader("Last-Modified", "")
            validator = etag if etag and not etag.startswith("W/") else modified
            current = dict(size=total, etag=etag, modified=modified, validator=validator)
            if meta and any(current[k] != meta[k] for k in ("size", "etag", "modified")):
                raise FatalError("文件版本发生变化，已停止，拒绝混合不同版本。")
            data, deadline = bytearray(), time.monotonic() + seconds
            while len(data) < end - start + 1:
                if stop and stop():
                    raise Cancelled()
                if seconds and time.monotonic() >= deadline:
                    self.close()
                    return bytes(data), current
                block = r.read1(min(16384, end - start + 1 - len(data)))
                if not block:
                    raise OSError("下载分块提前断开，将重试。")
                data.extend(block)
            return bytes(data), current
        except Exception:
            self.close()
            raise
