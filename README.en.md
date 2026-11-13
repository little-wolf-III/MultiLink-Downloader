# MultiLink-Downloader

[简体中文](README.md) | **English**

A Windows downloader with a Tkinter interface, automatic network adapter detection, and speed-based selection of up to **four network adapters** for a single file download.

**Version: 2.1**

## Requirements

- Windows 10 or Windows 11.
- Python 3.10 or later, with the **Tcl/Tk and IDLE** component installed.
- No third-party Python packages are required; the application uses the standard library.

> This is the English documentation. The v2.1 application interface is currently in Chinese; the main button labels are explained below.

## Quick start

1. Download and extract the project, then double-click `start.bat`.
   If you use a virtual environment, open a terminal in the project directory and run:

   ```bash
   python gui.py
   ```

2. The application automatically lists connected adapters with IPv4 addresses.
3. Enter a direct HTTP/HTTPS file URL in **下载直链**. Click **浏览…** to choose the destination file.
4. Leave **使用链路数** set to **自动** for automatic selection.
5. Click **开始 / 续传** to start or resume. The application checks the target, benchmarks candidate combinations, and starts downloading.

The URL must point directly to a file on a server that supports **HTTP Range requests**. Ordinary web page URLs are not supported. Accessible direct file links from services such as GitHub or Hugging Face may work if the server supports the required requests.

The application does not include web page or video extraction, browser login, CAPTCHA handling, or cookie import.

## Adapter detection and selection

- Detected adapters are candidates, not necessarily usable download routes. Each candidate is checked against the target URL when you start.
- Click the **候选** column to include or exclude an adapter, for example to avoid using a metered mobile connection.
- **Automatic mode:** compares single-adapter and multi-adapter combinations and selects the highest measured aggregate throughput. A single adapter is used if it performs best.
- **A specified count of 1, 2, 3, or 4:** selects the fastest measured combination with that exact number of adapters. If too few candidates pass the checks, the application reports an error.
- To force particular adapters, enable only those candidates and set the count to their number.
- If more than four adapters are available, the application benchmarks each individually, then compares combinations of the four fastest individual candidates. It does not exhaustively search all combinations of every adapter in the system.
- Each selected adapter uses two HTTP connections by default, giving up to eight connections across four adapters. Adapter count and connection count are different settings.
- Adapters sharing the same upstream connection may compete for the same bandwidth. More adapters do not necessarily mean higher speed.

### Benchmark limits

The default benchmark reading window is approximately 0.7 seconds, with at most 512 KiB read per connection. Testing all combinations of four candidates reads at most approximately **32 MiB of benchmark response bodies**, plus small probes and request overhead. Each additional candidate beyond four adds at most approximately 1 MiB for its individual benchmark.

Slow responses and network timeouts can make a benchmark take longer than its reading window. Preparation can be paused.

The selected combination reflects a short measurement against the specified file. It is not guaranteed to remain the fastest as conditions change. During downloading, the application updates speed estimates, disables persistently failing routes, and handles slow final chunks; it does not repeatedly benchmark every combination.

## Pause, resume, and exit

- Click **暂停** and wait for **已暂停** before exiting.
- To resume, use the same URL and destination path, then click **开始 / 续传**.
- You may change the adapter selection or count before resuming. The application checks and benchmarks the candidates again.
- Keep both `filename.part` and `filename.multilink.json`. Both are required for resuming.
- Completed chunks are checked against their saved hashes. Corrupted local chunks are downloaded again.
- Resuming across runs requires a server version identifier: a strong ETag or Last-Modified value. Files without such an identifier can be downloaded from scratch, but cannot safely be resumed across runs.
- Closing the window during a task requests a stop, saves progress, and then exits instead of immediately terminating download threads.
- After disconnecting or reconnecting an adapter, click **刷新网卡** to refresh its index and address before continuing.
- Version 1.4 `.dualmeta.json` resume files are **not compatible** with v2.1. Finish old tasks with v1.4 or start a fresh download under another filename.

## Download behavior and error handling

- The default chunk size is 128 KiB. Workers claim new chunks as they finish, so faster routes naturally handle more data.
- Near completion, the fastest route can take over chunks held by a slow route. Only the first successfully completed, validated copy is written.
- Persistently failing adapters are disabled and the remaining selected routes continue. The active adapter count may therefore decrease after a failure.
- If all selected routes become unavailable, the task stops and saves progress. Resume manually after restoring connectivity.
- An **HTTP 429** response stops the entire task. Wait before resuming; the application does not switch adapters to bypass server rate limits.
- Chunk responses must return HTTP 206 with matching Content-Range, length, and file version information.
- File size, version information, and samples from the beginning, middle, and end are compared across routes. Inconsistent files are not combined.
- A complete SHA-256 hash is calculated after download. If you enter a trusted expected value in **SHA-256（可选）**, it is compared automatically. Calculating a hash alone does not authenticate the source.
- Existing destination files are not overwritten. Choose another filename.
- If a `.lock` file prevents starting, first check that no other process is downloading to that path. If the previous process exited abnormally and no task remains, remove the stale lock and resume. Normal exits remove the lock automatically.

## Project files

| File | Purpose |
|---|---|
| `start.bat` | Windows launcher |
| `gui.py` | Tkinter interface; background work is separated from UI updates |
| `network.py` | Adapter discovery, IPv4 binding, HTTP/HTTPS transport, and Range validation |
| `downloader.py` | Benchmarking, route selection, up to four-adapter downloading, slow-chunk takeover, resume, and SHA-256 |
| `requirements.txt` | Runtime requirements; no third-party runtime dependencies |
| `测试报告.txt`, `tests/` | Test scope, test code, and recorded cloud results |

Successful downloads also produce `filename.report.json`, containing route selection, benchmark, and verification information.

## Optional command-line usage

GUI users do not need these commands.

```bash
python downloader.py --list
python downloader.py --url "https://example.com/file.bin" --out "file.bin"
python downloader.py --url "https://example.com/file.bin" --out "file.bin" --interfaces 9,12,15,18 --count 4
```

Replace the adapter indices with values reported on your own computer. The `example.com` URL demonstrates command syntax; it is not a working test download source.

## Validation scope

Cloud testing on Linux covered real HTTP requests, four distinct source IPs, actual file writes, and a real Tk interface.

**Windows PowerShell adapter discovery and actual Windows routing through IP_UNICAST_IF have not been verified on a Windows machine.** Cloud simulation does not replace testing physical adapters and real networks.

For your first local test, download a small, trusted file and check each adapter's transferred data and the completed file hash.

## License and commercial licensing

This project is licensed under the [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0). See [LICENSE](LICENSE) for the complete terms.

Noncommercial use, study, modification, and distribution are permitted under the license. Use by charitable organizations, educational institutions, public research organizations, and other listed organizations is governed by the license's expressly permitted purposes. Preserve the license and required copyright notices when distributing the software.

Except for uses already expressly permitted by the license, commercial use requires prior, separate authorization from the author. Publishing your source code does not, by itself, remove this requirement.

For commercial licensing inquiries, open an [Issue](https://github.com/little-wolf-III/MultiLink-Downloader/issues) with a brief description of your intended use. Do not post business secrets or sensitive personal information in a public issue.

This project is **source-available**, not open source under the OSI definition, because its license restricts commercial use. This section is a summary; the LICENSE and any separately executed licensing agreement govern the applicable rights and obligations.
