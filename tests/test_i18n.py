"""显示层翻译和配置测试，不访问网络、不修改用户配置。"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from i18n import BACKEND, PATTERNS, Preferences, TEXTS, backend_text, text


class LanguageTests(unittest.TestCase):
    def test_all_ui_keys_format_in_both_languages(self):
        values = dict(count=4, names="Wi-Fi + USB", sha="abc", message="oops", percent=50, done=1, total=2, speed=3)
        for key in TEXTS:
            for language in ("en", "zh"):
                with self.subTest(key=key, language=language):
                    value = text(key, language, **values)
                    self.assertTrue(value)
                    self.assertNotIn("{", value)
                    if language == "en":
                        self.assertIsNone(re.search(r"[\u4e00-\u9fff]", value))

    def test_backend_literals(self):
        for chinese, english in BACKEND.items():
            self.assertEqual(backend_text(chinese, "zh"), chinese)
            self.assertEqual(backend_text(chinese, "en"), english)
            self.assertIsNone(re.search(r"[\u4e00-\u9fff]", english))

    def test_dynamic_and_nested_backend_messages(self):
        samples = [
            "正在探测 4 张候选网卡……", "测速 Wi-Fi+USB：2.50 MiB/s",
            "指定 4 条链路，但只有 2 条通过探测和测速。",
            "Wi-Fi 返回的文件与其他线路不同，拒绝拼接。", "已选择：Wi-Fi + USB",
            "续传校验完成：保留 8.25 MiB，损坏块自动重下。", "开始下载；每路 2 个连接。",
            "HTTP 403，目标必须支持 Range 并返回 206。",
            "HTTP 429：已停止整个任务，Retry-After=未提供；稍后手动续传。",
            "Wi-Fi 连接失败：timed out",
            "Wi-Fi 连续失败，已停用：下载分块提前断开，将重试。",
            "不可用：请输入 HTTP/HTTPS 文件直链，不支持带用户名密码的 URL。",
            "测速失败：HTTP 503，目标必须支持 Range 并返回 206。",
            "组合测速失败，跳过：重定向次数过多。",
            "文件写入失败，请检查磁盘空间和权限：Disk full",
            "链路持续失败，已保存断点：HTTP 503，目标必须支持 Range 并返回 206。",
        ]
        self.assertEqual(len(samples), len(PATTERNS))
        for value in samples:
            with self.subTest(message=value):
                english = backend_text(value, "en")
                self.assertNotEqual(value, english)
                self.assertIsNone(re.search(r"[\u4e00-\u9fff]", english))
                self.assertEqual(backend_text(value, "zh"), value)

    def test_user_data_and_unknown_details_preserved(self):
        self.assertEqual(backend_text("已选择：中文网卡 + USB", "en"), "Selected: 中文网卡 + USB")
        self.assertEqual(backend_text("测速 中文网卡：2.50 MiB/s", "en"), "Benchmark 中文网卡: 2.50 MiB/s")
        raw = "[WinError 123] 原始系统错误 C:\\下载\\file.bin"
        self.assertEqual(backend_text(raw, "en"), raw)

    def test_language_preference_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Preferences(Path(temp) / "nested/settings.json")
            for language in ("zh", "en"):
                p.save(language)
                self.assertEqual(Preferences(p.path).load(), language)
            self.assertEqual(json.loads(p.path.read_text()), {"language": "en"})

    def test_missing_or_corrupt_preferences_use_default(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Preferences(Path(temp) / "settings.json")
            with patch("i18n.default_language", return_value="en"):
                self.assertEqual(p.load(), "en")
                for content in ("broken", "[]", '{"language":"xx"}'):
                    p.path.write_text(content)
                    self.assertEqual(p.load(), "en")


if __name__ == "__main__":
    unittest.main(verbosity=2)
