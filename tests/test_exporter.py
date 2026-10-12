"""分开保存：文件名带日期-时分秒、同秒不重名、绝不覆盖、失败不丢图。"""

from __future__ import annotations

import time
import unittest

from clipkit import exporter
from tests import support

WHEN = time.mktime((2026, 10, 12, 14, 3, 40, 0, 0, -1))


class UniquePathTests(unittest.TestCase):
    def test_filename_is_date_and_time(self):
        with support.temp_dir() as d:
            self.assertEqual(exporter.unique_path(d, WHEN).name, "2026-10-12_14-03-40.png")

    def test_same_second_gets_suffix(self):
        with support.temp_dir() as d:
            first = exporter.unique_path(d, WHEN)
            first.write_bytes(b"x")
            self.assertEqual(exporter.unique_path(d, WHEN).name, "2026-10-12_14-03-40_2.png")

    def test_used_names_are_avoided_too(self):
        with support.temp_dir() as d:
            used = {"2026-10-12_14-03-40.png"}
            self.assertEqual(exporter.unique_path(d, WHEN, used).name, "2026-10-12_14-03-40_2.png")


class SaveShotsTests(unittest.TestCase):
    def test_saves_each_shot_with_its_own_time(self):
        with support.temp_dir() as d:
            shots = [exporter.Shot(support.block(20, 10, (2, 2, 18, 8)), WHEN + i) for i in range(3)]
            paths = exporter.save_shots(shots, d)
            self.assertEqual([p.name for p in paths],
                             ["2026-10-12_14-03-40.png",
                              "2026-10-12_14-03-41.png",
                              "2026-10-12_14-03-42.png"])
            self.assertTrue(all(p.exists() for p in paths))

    def test_same_second_shots_do_not_collide(self):
        with support.temp_dir() as d:
            shots = [exporter.Shot(support.blank(8, 8), WHEN) for _ in range(3)]
            paths = exporter.save_shots(shots, d)
            self.assertEqual(len({p.name for p in paths}), 3)
            self.assertIn("2026-10-12_14-03-40_2.png", [p.name for p in paths])

    def test_creates_missing_folder(self):
        with support.temp_dir() as d:
            target = d / "sub" / "shots"
            exporter.save_images([support.block(10, 10, (1, 1, 9, 9))], target)
            self.assertTrue(target.is_dir())

    def test_existing_file_is_never_overwritten(self):
        with support.temp_dir() as d:
            exporter.save_shots([exporter.Shot(support.blank(5, 5), WHEN)], d)
            again = exporter.save_shots([exporter.Shot(support.blank(6, 6), WHEN)], d)
            self.assertEqual(again[0].name, "2026-10-12_14-03-40_2.png")

    def test_default_folder_is_named_clipkit(self):
        self.assertEqual(exporter.default_folder().name, "clipkit")


if __name__ == "__main__":
    unittest.main()
