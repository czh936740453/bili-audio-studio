"""界面静态资源一致性检查（无需浏览器）。

覆盖两类最容易导致白屏的问题：
  1. JS 引用了 index.html 里不存在的元素 id；
  2. 页面引用的静态文件缺失。
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from biliaudio.config import WEB_DIR  # noqa: E402

# 由弹窗动态生成、不会出现在 index.html 中的元素
DYNAMIC_IDS = {
    "set-sessdata", "set-quality", "set-bitrate", "set-format",
    "set-parallel", "set-output", "new-project-name", "btn-create-project",
}


class TestWebAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        cls.js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        cls.css = (WEB_DIR / "style.css").read_text(encoding="utf-8")
        cls.html_ids = set(re.findall(r'\bid="([^"]+)"', cls.html))

    def test_static_files_exist(self):
        for name in ("index.html", "app.js", "style.css"):
            self.assertTrue((WEB_DIR / name).is_file(), name)
        self.assertIn('/static/app.js', self.html)
        self.assertIn('/static/style.css', self.html)

    def test_referenced_ids_exist(self):
        used = set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", self.js))
        used |= set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', self.js))
        missing = sorted(used - self.html_ids - DYNAMIC_IDS)
        self.assertEqual(missing, [], f"JS 引用了不存在的 id：{missing}")

    def test_html_ids_are_unique(self):
        all_ids = re.findall(r'\bid="([^"]+)"', self.html)
        self.assertEqual(len(all_ids), len(set(all_ids)), "index.html 存在重复 id")

    def test_css_classes_used(self):
        # 抽查 JS 里拼装的关键 class 在样式表中确实有定义
        for cls in ("track", "row-play", "tl-tick", "logbox", "toast", "modal", "volume-range", "loop-one"):
            self.assertIn(f".{cls}", self.css, cls)

    def test_loop_button_present(self):
        self.assertIn('id="btn-loop"', self.html)
        self.assertIn("cycleLoop", self.js)
        self.assertIn("localStorage.setItem('bst_loop'", self.js)

    def test_api_paths_declared(self):
        # 前端调用的接口前缀都应能在后端路由里找到
        server_py = (ROOT / "biliaudio" / "server.py").read_text(encoding="utf-8")
        heads = set()
        for match in re.finditer(r"/api/[A-Za-z0-9_/\-]+", self.js):
            heads.add(match.group(0).rstrip("/"))
        self.assertTrue(heads, "前端没有任何接口调用？")
        for head in sorted(heads):
            self.assertIn(head, server_py, f"后端缺少接口：{head}")

    def test_no_progress_bar(self):
        # 界面改成只列曲目，不再有进度条
        self.assertNotIn("progress-bar", self.html)
        self.assertNotIn("progress-wrap", self.html)
        self.assertNotIn("progress-bar", self.js)
        self.assertIn('id="job-status"', self.html)

    def test_no_legacy_syntax(self):
        # 避免误用 Python 风格的写法/未闭合模板字符串
        self.assertEqual(self.js.count("`") % 2, 0, "模板字符串反引号不成对")


if __name__ == "__main__":
    unittest.main()
