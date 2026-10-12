"""配置读写：默认值、合并写回、坏文件不致命。"""

from __future__ import annotations

import unittest
from unittest import mock

from clipkit import config
from tests import support


class ConfigTests(unittest.TestCase):
    def use(self, path):
        """把配置指向工作区里的临时文件（不碰真实的 %APPDATA%）。"""
        return mock.patch.dict("os.environ", {"CLIPKIT_CONFIG": str(path)})

    def test_defaults_when_file_missing(self):
        with support.temp_dir() as d, self.use(d / "cfg.json"):
            data = config.load()
            self.assertTrue(data["process_on_enable"])
            self.assertEqual(data["save_dir"], "")

    def test_update_then_load_roundtrip(self):
        with support.temp_dir() as d, self.use(d / "cfg.json"):
            self.assertTrue(config.update(save_dir=str(d), process_on_enable=False))
            self.assertEqual(config.save_dir(), d)
            self.assertFalse(config.load()["process_on_enable"])

    def test_broken_file_falls_back_to_defaults(self):
        with support.temp_dir() as d, self.use(d / "cfg.json"):
            (d / "cfg.json").write_text("{ 这不是 json", encoding="utf-8")
            self.assertTrue(config.load()["process_on_enable"])

    def test_unknown_keys_are_ignored(self):
        with support.temp_dir() as d, self.use(d / "cfg.json"):
            config.update(save_dir=str(d))
            config.update(nonsense=1)
            self.assertNotIn("nonsense", config.load())

    def test_env_var_selects_the_config_file(self):
        with support.temp_dir() as d, self.use(d / "cfg.json"):
            self.assertEqual(config.config_path(), d / "cfg.json")


if __name__ == "__main__":
    unittest.main()
