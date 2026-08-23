"""验证 BK-tree 候选搜索和非传递图片分组。"""

from itertools import combinations, islice
from pathlib import Path
import random
import time
import unittest
from unittest.mock import patch

from PIL import Image

from tools.image_similarity.grouping import (
    BKTree,
    GroupingCancelled,
    GroupingMetrics,
    build_similarity_groups,
    is_visually_similar,
)
from tools.image_similarity.models import (
    GroupType,
    ImageFingerprint,
    ImageRecord,
    SimilarityPreset,
    thresholds_for,
)


class ImageSimilarityGroupingTests(unittest.TestCase):
    """使用合成指纹隔离验证分组规则。"""

    def _fingerprint(
        self,
        name,
        *,
        phash=0,
        dhash=0,
        gray=100,
        grayscale=None,
        rgba=None,
        sha256=None,
        width=100,
        height=100,
        size=1000,
        order=0,
    ):
        """构造不访问文件系统的稳定测试指纹。"""
        path = Path("C:/scan") / name
        record = ImageRecord(
            path=path,
            canonical_path=path,
            size_bytes=size,
            mtime_ns=1,
            image_format="PNG",
            width=width,
            height=height,
            order=order,
        )
        return ImageFingerprint(
            record=record,
            phash=phash,
            dhash=dhash,
            grayscale=grayscale if grayscale is not None else bytes([gray]) * 256,
            rgba=rgba if rgba is not None else b"",
            sha256=sha256,
        )

    def test_exact_duplicates_form_exact_group(self):
        """相同 SHA-256 的成员形成完全重复组并全部展开。"""
        first = self._fingerprint("a.png", sha256="same", order=0)
        second = self._fingerprint("b.png", sha256="same", order=1)

        groups = build_similarity_groups(
            (second, first),
            SimilarityPreset.STRICT,
        )

        self.assertEqual(1, len(groups))
        self.assertEqual(GroupType.EXACT, groups[0].group_type)
        self.assertEqual((first, second), groups[0].members)

    def test_similarity_does_not_expand_transitively(self):
        """候选只与种子比较，不能依靠 B 把 C 传递进 A 组。"""
        first = self._fingerprint("a.png", phash=0, dhash=0, gray=100, order=0)
        bridge = self._fingerprint(
            "b.png",
            phash=0b1111,
            dhash=0b1111,
            gray=105,
            order=1,
        )
        last = self._fingerprint(
            "c.png",
            phash=0b11111111,
            dhash=0b11111111,
            gray=110,
            order=2,
        )

        self.assertTrue(
            is_visually_similar(
                first,
                bridge,
                thresholds_for(SimilarityPreset.STRICT),
            )
        )
        self.assertTrue(
            is_visually_similar(
                bridge,
                last,
                thresholds_for(SimilarityPreset.STRICT),
            )
        )
        self.assertFalse(
            is_visually_similar(
                first,
                last,
                thresholds_for(SimilarityPreset.STRICT),
            )
        )

        groups = build_similarity_groups(
            (first, bridge, last),
            SimilarityPreset.STRICT,
        )

        self.assertEqual(1, len(groups))
        self.assertEqual((first, bridge), groups[0].members)
        self.assertNotIn(last, groups[0].members)

    def test_grayscale_recheck_prevents_single_hash_false_positive(self):
        """感知哈希相同但灰度差异明显的图片不能直接归组。"""
        dark = self._fingerprint("dark.png", gray=10)
        light = self._fingerprint("light.png", gray=245, order=1)

        self.assertFalse(
            is_visually_similar(
                dark,
                light,
                thresholds_for(SimilarityPreset.LOOSE),
            )
        )

    def test_representative_prefers_pixels_then_size_then_path(self):
        """建议保留项稳定偏好更大像素、文件大小和路径顺序。"""
        small = self._fingerprint(
            "z.png",
            width=100,
            height=100,
            size=5000,
            order=0,
        )
        large_small_file = self._fingerprint(
            "b.png",
            width=200,
            height=200,
            size=2000,
            order=1,
        )
        large_big_file = self._fingerprint(
            "a.png",
            width=200,
            height=200,
            size=3000,
            order=2,
        )

        groups = build_similarity_groups(
            (small, large_small_file, large_big_file),
            SimilarityPreset.STRICT,
        )

        self.assertEqual(1, len(groups))
        self.assertEqual(large_big_file, groups[0].suggested_keep)
        self.assertEqual(large_big_file, groups[0].representative)

    def test_bk_tree_reduces_search_for_ten_thousand_hashes(self):
        """BK-tree 查询不能退化为固定的一万次全量比较。"""
        mask = (1 << 64) - 1
        values = [
            (index * 11400714819323198485) & mask
            for index in range(10_000)
        ]
        tree = BKTree()
        for index, value in enumerate(values):
            tree.add(value, index)

        matches = tree.search(values[1234], radius=4)

        self.assertIn(1234, matches)
        self.assertLess(tree.last_search_comparisons, 10_000)

    def test_same_phash_collision_uses_dhash_index_for_ten_thousand_items(self):
        """大量相同 pHash 但离散 dHash 不能触发近一亿次候选复核。"""
        mask = (1 << 64) - 1
        fingerprints = tuple(
            self._fingerprint(
                f"collision-{index}.png",
                phash=0,
                dhash=(index * 11400714819323198485) & mask,
                gray=index % 256,
                order=index,
            )
            for index in range(10_000)
        )
        metrics = GroupingMetrics()
        started = time.monotonic()

        groups = build_similarity_groups(
            fingerprints,
            SimilarityPreset.STRICT,
            metrics=metrics,
        )

        self.assertEqual((), groups)
        self.assertLess(metrics.candidate_rechecks, 100_000)
        self.assertLess(time.monotonic() - started, 10.0)

    def test_grouping_honors_cooperative_cancellation(self):
        """分组建索引和候选复核期间应持续检查协作取消信号。"""
        fingerprints = tuple(
            self._fingerprint(
                f"cancel-{index}.png",
                phash=index,
                dhash=index,
                order=index,
            )
            for index in range(500)
        )
        checks = 0

        def cancelled():
            """在若干内部检查后模拟用户请求取消。"""
            nonlocal checks
            checks += 1
            return checks > 20

        with self.assertRaises(GroupingCancelled):
            build_similarity_groups(
                fingerprints,
                SimilarityPreset.STRICT,
                cancelled=cancelled,
            )

        self.assertLess(checks, len(fingerprints))

    def test_ten_thousand_double_hash_collisions_use_grayscale_index(self):
        """一万条双哈希碰撞也不能逐对执行完整灰度复核。"""
        generator = random.Random(20260823)
        fingerprints = tuple(
            self._fingerprint(
                f"double-collision-{index}.png",
                phash=0,
                dhash=0,
                grayscale=generator.randbytes(256),
                order=index,
            )
            for index in range(10_000)
        )
        metrics = GroupingMetrics()
        started = time.monotonic()

        groups = build_similarity_groups(
            fingerprints,
            SimilarityPreset.STRICT,
            metrics=metrics,
        )

        self.assertEqual((), groups)
        self.assertLess(metrics.candidate_rechecks, 100_000)
        self.assertLess(time.monotonic() - started, 20.0)

    def test_dense_gray_collisions_rejected_by_color_do_not_scan_all_pairs(self):
        """灰度相同但颜色不同的密集桶不能在颜色复核处退化成全量两两比较。"""
        generator = random.Random(20260827)
        equal_luminance_palette = []
        for red in range(256):
            for green in range(256):
                blue = round((128_000 - 299 * red - 587 * green) / 114)
                if not 0 <= blue <= 255:
                    continue
                color = (red, green, blue)
                if (
                    Image.new("RGB", (1, 1), color)
                    .convert("L")
                    .getpixel((0, 0))
                    == 128
                ):
                    equal_luminance_palette.append((*color, 255))

        def opaque_rgba_descriptor():
            """生成每个像素均为同亮度异色的生产 8×8 RGBA 描述。"""
            return bytes(
                channel
                for _ in range(64)
                for channel in generator.choice(equal_luminance_palette)
            )

        fingerprints = tuple(
            self._fingerprint(
                f"color-reject-{index}.png",
                phash=0,
                dhash=0,
                gray=128,
                rgba=opaque_rgba_descriptor(),
                order=index,
            )
            for index in range(10_000)
        )
        metrics = GroupingMetrics()
        started = time.monotonic()

        groups = build_similarity_groups(
            fingerprints,
            SimilarityPreset.STRICT,
            metrics=metrics,
        )

        self.assertEqual((), groups)
        self.assertLess(metrics.candidate_rechecks, 5_000)
        self.assertLess(metrics.projection_candidates_examined, 5_000_000)
        self.assertLess(time.monotonic() - started, 10.0)

    def test_random_alpha_white_collision_uses_common_channel_projection(self):
        """共同通道变化必须由颜色和投影过滤且保持完整候选语义。"""
        generator = random.Random(20260829)
        fingerprints = tuple(
            self._fingerprint(
                f"alpha-collision-{index}.png",
                phash=0,
                dhash=0,
                gray=255,
                rgba=bytes(
                    channel
                    for alpha in generator.randbytes(64)
                    for channel in (alpha, alpha, alpha, alpha)
                ),
                order=index,
            )
            for index in range(10_000)
        )
        metrics = GroupingMetrics()
        started = time.monotonic()

        groups = build_similarity_groups(
            fingerprints,
            SimilarityPreset.STRICT,
            metrics=metrics,
        )

        self.assertEqual((), groups)
        self.assertLess(metrics.projection_candidates_examined, 5_000_000)
        self.assertLess(time.monotonic() - started, 10.0)

    def test_dense_grayscale_index_queries_one_seed_at_a_time(self):
        """密集灰度邻域必须按当前代表图惰性查询，不能预存全部邻接表。"""
        query_shapes = []

        class RecordingTree:
            """记录灰度索引每次查询的数据形状。"""

            def __init__(self, _matrix):
                """接受建树矩阵但不执行真实空间索引。"""

            def query_ball_point(self, points, **_kwargs):
                """返回空邻域，让测试只关注查询是否保持惰性。"""
                query_shapes.append(points.shape)
                if len(points.shape) == 1:
                    return []
                return [[] for _ in range(len(points))]

        fingerprints = tuple(
            self._fingerprint(
                f"dense-gray-{index}.png",
                phash=0,
                dhash=0,
                gray=index,
                order=index,
            )
            for index in range(33)
        )

        with patch("tools.image_similarity.grouping.cKDTree", RecordingTree):
            groups = build_similarity_groups(
                fingerprints,
                SimilarityPreset.STRICT,
            )

        self.assertEqual((), groups)
        self.assertTrue(query_shapes)
        self.assertEqual((32,), query_shapes[0])
        self.assertTrue(
            all(
                len(shape) == 1 or shape[0] <= 64
                for shape in query_shapes
            )
        )

    def test_loose_grayscale_index_uses_stronger_lower_bound_projection(self):
        """宽松阈值应增加下界分块数，避免低维投影交付近全量候选。"""
        tree_shapes = []

        class RecordingTree:
            """只记录建树维度并返回空邻域。"""

            def __init__(self, matrix):
                tree_shapes.append(matrix.shape)

            def query_ball_point(self, points, **_kwargs):
                if len(points.shape) == 1:
                    return []
                return [[] for _ in range(len(points))]

        fingerprints = tuple(
            self._fingerprint(
                f"loose-gray-{index}.png",
                phash=0,
                dhash=0,
                gray=index,
                order=index,
            )
            for index in range(33)
        )

        with patch("tools.image_similarity.grouping.cKDTree", RecordingTree):
            build_similarity_groups(
                fingerprints,
                SimilarityPreset.LOOSE,
            )

        self.assertTrue(tree_shapes)
        self.assertEqual(64, tree_shapes[0][1])

    def test_dense_hash_radius_switches_to_grayscale_index(self):
        """近邻哈希虽不完全相同，也不能让每个种子重复扫描整个密集邻域。"""
        generator = random.Random(20260824)
        phashes = tuple(
            sum(1 << bit for bit in bit_indexes)
            for bit_indexes in islice(combinations(range(64), 4), 500)
        )
        fingerprints = tuple(
            self._fingerprint(
                f"near-collision-{index}.png",
                phash=phash,
                dhash=0,
                grayscale=generator.randbytes(256),
                order=index,
            )
            for index, phash in enumerate(phashes)
        )
        metrics = GroupingMetrics()

        groups = build_similarity_groups(
            fingerprints,
            SimilarityPreset.STANDARD,
            metrics=metrics,
        )

        self.assertEqual((), groups)
        self.assertLess(metrics.candidate_rechecks, 10_000)
        self.assertLess(metrics.index_candidates_examined, 10_000)

    def test_grayscale_index_includes_exact_standard_threshold_boundary(self):
        """灰度索引必须交付最终相似度恰好等于阈值的整数距离候选。"""
        boundary_gray = bytes([26]) * 128 + bytes([25]) * 128
        fingerprints = [
            self._fingerprint(
                "boundary-a.png",
                phash=0,
                dhash=0,
                grayscale=bytes(256),
                order=0,
            ),
            self._fingerprint(
                "boundary-b.png",
                phash=0,
                dhash=0,
                grayscale=boundary_gray,
                order=1,
            ),
        ]
        fingerprints.extend(
            self._fingerprint(
                f"far-{index:02d}.png",
                phash=0,
                dhash=0,
                grayscale=bytes([255]) * 256,
                order=index + 2,
            )
            for index in range(31)
        )

        groups = build_similarity_groups(
            tuple(fingerprints),
            SimilarityPreset.STANDARD,
        )

        boundary_paths = {
            fingerprints[0].record.path,
            fingerprints[1].record.path,
        }
        self.assertTrue(
            any(
                boundary_paths.issubset(
                    {member.record.path for member in group.members}
                )
                for group in groups
            )
        )

    def test_color_first_index_keeps_candidate_near_strict_boundary(self):
        """切换为颜色优先后仍不能漏掉严格颜色阈值内的真实候选。"""
        near_boundary_rgba = bytes([16]) * 76 + bytes([15]) * 180
        fingerprints = [
            self._fingerprint(
                "color-boundary-a.png",
                phash=0,
                dhash=0,
                gray=128,
                rgba=bytes(256),
                order=0,
            ),
            self._fingerprint(
                "color-boundary-b.png",
                phash=0,
                dhash=0,
                gray=128,
                rgba=near_boundary_rgba,
                order=1,
            ),
        ]
        fingerprints.extend(
            self._fingerprint(
                f"color-far-{index:02d}.png",
                phash=0,
                dhash=0,
                gray=128,
                rgba=bytes([255]) * 256,
                order=index + 2,
            )
            for index in range(31)
        )

        groups = build_similarity_groups(
            tuple(fingerprints),
            SimilarityPreset.STRICT,
        )

        boundary_paths = {
            fingerprints[0].record.path,
            fingerprints[1].record.path,
        }
        self.assertTrue(
            any(
                boundary_paths.issubset(
                    {member.record.path for member in group.members}
                )
                for group in groups
            )
        )

    def test_dense_hash_region_does_not_force_unrelated_seeds_to_grayscale(self):
        """一次局部哈希碰撞不能让后续无关区域永久改走更稠密的灰度索引。"""
        generator = random.Random(20260825)
        dense_hash_region = tuple(
            self._fingerprint(
                f"a-dense-hash-{index:02d}.png",
                phash=0,
                dhash=0,
                grayscale=generator.randbytes(256),
                order=index,
            )
            for index in range(40)
        )
        sparse_hash_region = tuple(
            self._fingerprint(
                f"z-dense-gray-{index:03d}.png",
                phash=generator.getrandbits(64),
                dhash=generator.getrandbits(64),
                grayscale=bytes([128]) * 256,
                order=index + 40,
            )
            for index in range(120)
        )
        metrics = GroupingMetrics()

        groups = build_similarity_groups(
            dense_hash_region + sparse_hash_region,
            SimilarityPreset.STANDARD,
            metrics=metrics,
        )

        self.assertEqual((), groups)
        self.assertLess(metrics.index_candidates_examined, 2_000)
        self.assertLess(metrics.hash_filter_checks, 2_000)

    def test_dense_phash_region_uses_sparse_dhash_before_gray_propagation(self):
        """pHash 半径密集但 dHash 稀疏时不能把整个区域永久切换到灰度索引。"""
        mask = (1 << 64) - 1
        phashes = (0,) + tuple(
            sum(1 << bit for bit in bit_indexes)
            for bit_indexes in islice(combinations(range(64), 4), 499)
        )
        fingerprints = tuple(
            self._fingerprint(
                f"single-dense-hash-{index:03d}.png",
                phash=phash,
                dhash=(index * 11400714819323198485) & mask,
                grayscale=(bytes(256) if index == 0 else bytes([128]) * 256),
                order=index,
            )
            for index, phash in enumerate(phashes)
        )
        metrics = GroupingMetrics()

        groups = build_similarity_groups(
            fingerprints,
            SimilarityPreset.STANDARD,
            metrics=metrics,
        )

        self.assertEqual((), groups)
        self.assertLess(metrics.index_candidates_examined, 50_000)
        self.assertLess(metrics.hash_filter_checks, 50_000)

    def test_star_hash_neighborhood_does_not_propagate_to_outer_members(self):
        """双哈希只接近中心的星形叶节点不能继承中心的灰度索引偏好。"""
        generator = random.Random(20260826)
        phash_value_set = set()
        dhash_value_set = set()
        while len(phash_value_set) < 499:
            phash_value_set.add(
                sum(1 << bit for bit in generator.sample(range(64), 8))
            )
        while len(dhash_value_set) < 499:
            dhash_value_set.add(
                sum(1 << bit for bit in generator.sample(range(64), 8))
            )
        phash_values = tuple(sorted(phash_value_set))
        dhash_values = tuple(sorted(dhash_value_set, reverse=True))
        fingerprints = [
            self._fingerprint(
                "star-center.png",
                phash=0,
                dhash=0,
                grayscale=bytes(256),
                order=0,
            )
        ]
        fingerprints.extend(
            self._fingerprint(
                f"star-leaf-{index:03d}.png",
                phash=phash,
                dhash=dhash,
                grayscale=bytes([128]) * 256,
                order=index,
            )
            for index, (phash, dhash) in enumerate(
                zip(phash_values, dhash_values),
                start=1,
            )
        )
        metrics = GroupingMetrics()

        build_similarity_groups(
            tuple(fingerprints),
            SimilarityPreset.STANDARD,
            metrics=metrics,
        )

        self.assertLess(metrics.index_candidates_examined, 50_000)
        self.assertLess(metrics.hash_filter_checks, 50_000)

    def test_ten_thousand_identical_gray_fingerprints_do_not_store_all_edges(self):
        """一万张纯色近似图只查询首个密集邻域，不能保存平方级邻接关系。"""
        fingerprints = tuple(
            self._fingerprint(
                f"pure-color-{index}.png",
                phash=0,
                dhash=0,
                gray=128,
                order=index,
            )
            for index in range(10_000)
        )
        metrics = GroupingMetrics()
        started = time.monotonic()

        groups = build_similarity_groups(
            fingerprints,
            SimilarityPreset.STRICT,
            metrics=metrics,
        )

        self.assertEqual(1, len(groups))
        self.assertEqual(10_000, len(groups[0].members))
        self.assertLess(metrics.index_candidates_examined, 25_000)
        self.assertLess(time.monotonic() - started, 10.0)


if __name__ == "__main__":
    unittest.main()
