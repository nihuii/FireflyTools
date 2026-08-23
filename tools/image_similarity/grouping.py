"""使用 BK-tree 和代表图直接匹配规则构建安全相似组。"""

from dataclasses import dataclass, field
import math
from typing import Callable

import numpy as np
from scipy.spatial import cKDTree

from tools.image_similarity.fingerprints import (
    aspect_ratio_difference,
    grayscale_similarity,
    hamming_distance,
)
from tools.image_similarity.models import (
    GroupType,
    ImageFingerprint,
    SimilarityGroup,
    SimilarityPreset,
    SimilarityThresholds,
    thresholds_for,
)


@dataclass
class GroupingMetrics:
    """记录分组阶段实际进入完整相似度复核的候选数量。"""

    candidate_rechecks: int = 0
    index_candidates_examined: int = 0
    hash_filter_checks: int = 0
    projection_candidates_examined: int = 0


class GroupingCancelled(Exception):
    """表示分组阶段收到协作取消请求，不携带可展示的业务错误。"""


def _raise_if_cancelled(cancelled: Callable[[], bool] | None) -> None:
    """在分组的长循环边界检查取消并中止当前纯计算。"""
    if cancelled is not None and cancelled():
        raise GroupingCancelled()


@dataclass
class _BKNode:
    """保存 BK-tree 中一个哈希值、对应项目及距离子节点。"""

    value: object
    item_indexes: list[int] = field(default_factory=list)
    children: dict[int, "_BKNode"] = field(default_factory=dict)


class BKTree:
    """按汉明距离索引整数感知哈希，缩小候选复核范围。"""

    def __init__(
        self,
        distance: Callable[[object, object], int] = hamming_distance,
    ):
        """创建空索引并初始化最近一次查询的比较计数。"""
        self._root: _BKNode | None = None
        self._distance = distance
        self.last_search_comparisons = 0

    def add(self, value: object, item_index: int) -> None:
        """把一个哈希和业务索引加入 BK-tree。"""
        if self._root is None:
            self._root = _BKNode(value=value, item_indexes=[item_index])
            return

        node = self._root
        while True:
            distance = self._distance(value, node.value)
            if distance == 0:
                node.item_indexes.append(item_index)
                return
            child = node.children.get(distance)
            if child is None:
                node.children[distance] = _BKNode(
                    value=value,
                    item_indexes=[item_index],
                )
                return
            node = child

    def search(
        self,
        value: object,
        radius: int,
        cancelled: Callable[[], bool] | None = None,
        maximum_results: int | None = None,
    ) -> tuple[int, ...]:
        """返回汉明距离不超过半径的业务索引，并记录比较次数。"""
        self.last_search_comparisons = 0
        if self._root is None:
            return ()

        matches: list[int] = []
        stack = [self._root]
        while stack:
            _raise_if_cancelled(cancelled)
            node = stack.pop()
            self.last_search_comparisons += 1
            distance = self._distance(value, node.value)
            if distance <= radius:
                for item_index in node.item_indexes:
                    matches.append(item_index)
                    if (
                        maximum_results is not None
                        and len(matches) >= maximum_results
                    ):
                        return tuple(sorted(matches))

            lower = max(0, distance - radius)
            upper = distance + radius
            for edge_distance, child in node.children.items():
                if lower <= edge_distance <= upper:
                    stack.append(child)

        return tuple(sorted(matches))


@dataclass(frozen=True)
class _FingerprintCluster:
    """在视觉搜索前把完全重复成员收缩成一个代表单元。"""

    members: tuple[ImageFingerprint, ...]
    representative: ImageFingerprint


class _ByteL1CandidateIndex:
    """用分块和下界树索引定长字节描述，并以原始 L1 距离精确收口。"""

    def __init__(
        self,
        matrix: np.ndarray,
        maximum_distance: float,
        block_count: int,
        cancelled: Callable[[], bool] | None,
    ):
        """建立不会排除真实近邻的分块和投影。"""
        descriptor_length = matrix.shape[1]
        if descriptor_length % block_count != 0:
            raise ValueError("字节描述长度必须能被下界分块数整除")
        self._raw_matrix = matrix
        self._matrix = matrix.astype(np.int32).reshape(
            len(matrix),
            block_count,
            descriptor_length // block_count,
        ).sum(axis=2, dtype=np.int32)
        _raise_if_cancelled(cancelled)
        self._tree = cKDTree(self._matrix)
        self._maximum_distance = maximum_distance

    def _exact_matches(
        self,
        item_index: int,
        candidate_indexes,
    ) -> tuple[int, ...]:
        """对投影候选执行向量化原始字节 L1 精确复核。"""
        indexes = np.asarray(candidate_indexes, dtype=np.intp)
        if indexes.size == 0:
            return ()
        differences = np.abs(
            self._raw_matrix[indexes].astype(np.int16)
            - self._raw_matrix[item_index].astype(np.int16)
        )
        distances = differences.sum(axis=1, dtype=np.int32)
        matches = indexes[distances <= self._maximum_distance]
        return tuple(sorted(int(index) for index in matches))

    def search(
        self,
        item_index: int,
        cancelled: Callable[[], bool] | None,
    ) -> tuple[int, ...]:
        """返回原始字节 L1 半径内的精确候选。"""
        _raise_if_cancelled(cancelled)
        candidate_indexes = self._tree.query_ball_point(
            self._matrix[item_index],
            r=self._maximum_distance,
            p=1,
            workers=4,
        )
        return self._exact_matches(item_index, candidate_indexes)

    def search_many(
        self,
        item_indexes: tuple[int, ...],
        cancelled: Callable[[], bool] | None,
    ) -> dict[int, tuple[int, ...]]:
        """批量查询少量代表图，避免为每张图片单独进入空间树。"""
        if not item_indexes:
            return {}
        _raise_if_cancelled(cancelled)
        batch_matches = self._tree.query_ball_point(
            self._matrix[list(item_indexes)],
            r=self._maximum_distance,
            p=1,
            workers=4,
        )
        _raise_if_cancelled(cancelled)
        return {
            item_index: self._exact_matches(item_index, candidates)
            for item_index, candidates in zip(item_indexes, batch_matches)
        }


class _RgbaL1CandidateIndex:
    """用三组互补色通道下界过滤 RGBA，同时保留原始 L1 精确语义。"""

    _CHANNEL_PROJECTIONS = (
        (0, 1, 1, 2, 3, 1),
        (0, 1, -1, 2, 3, -1),
        (0, 2, -1, 1, 3, -1),
    )
    _PROJECTION_REFINEMENT_LIMIT = 512

    def __init__(
        self,
        matrix: np.ndarray,
        maximum_distance: float,
        projection_dimensions: int,
        cancelled: Callable[[], bool] | None,
    ):
        """建立一组通道和、两组通道差投影，每组都是原始 L1 的下界。"""
        if matrix.shape[1] % 4 != 0 or projection_dimensions % 2 != 0:
            raise ValueError("RGBA 描述和投影维度必须保持完整像素及通道对")
        pixel_count = matrix.shape[1] // 4
        group_count = projection_dimensions // 2
        if pixel_count % group_count != 0:
            raise ValueError("RGBA 像素数必须能被投影分组数整除")

        self._raw_matrix = matrix
        self._maximum_distance = maximum_distance
        self.last_candidates_examined = 0
        self._tree_order: tuple[int, ...] | None = None
        pixels = matrix.astype(np.int32).reshape(len(matrix), pixel_count, 4)
        pixels_per_group = pixel_count // group_count
        self._matrices = []
        self._trees = []
        for (
            first,
            second,
            second_sign,
            third,
            fourth,
            fourth_sign,
        ) in self._CHANNEL_PROJECTIONS:
            per_pixel = np.stack(
                (
                    pixels[:, :, first]
                    + second_sign * pixels[:, :, second],
                    pixels[:, :, third]
                    + fourth_sign * pixels[:, :, fourth],
                ),
                axis=2,
            )
            projection = per_pixel.reshape(
                len(matrix),
                group_count,
                pixels_per_group,
                2,
            ).sum(axis=2, dtype=np.int32).reshape(
                len(matrix),
                projection_dimensions,
            )
            _raise_if_cancelled(cancelled)
            self._matrices.append(projection)
            self._trees.append(cKDTree(projection))

    def _exact_matches(
        self,
        item_index: int,
        candidate_indexes,
    ) -> tuple[int, ...]:
        """以原始预乘 RGBA 字节执行最终 L1 精确过滤。"""
        indexes = np.asarray(tuple(candidate_indexes), dtype=np.intp)
        if indexes.size == 0:
            return ()
        differences = np.abs(
            self._raw_matrix[indexes].astype(np.int16)
            - self._raw_matrix[item_index].astype(np.int16)
        )
        distances = differences.sum(axis=1, dtype=np.int32)
        matches = indexes[distances <= self._maximum_distance]
        return tuple(sorted(int(index) for index in matches))

    def search(
        self,
        item_index: int,
        cancelled: Callable[[], bool] | None,
    ) -> tuple[int, ...]:
        """按首个种子选择投影顺序，再按需取交集并以原始 RGBA 收口。"""
        candidates: set[int] | None = None
        self.last_candidates_examined = 0
        if self._tree_order is None:
            initial_matches = []
            for matrix, tree in zip(self._matrices, self._trees):
                _raise_if_cancelled(cancelled)
                matches = set(
                    tree.query_ball_point(
                        matrix[item_index],
                        r=self._maximum_distance,
                        p=1,
                        workers=4,
                    )
                )
                self.last_candidates_examined += len(matches)
                initial_matches.append(matches)
            self._tree_order = tuple(
                sorted(
                    range(len(initial_matches)),
                    key=lambda index: len(initial_matches[index]),
                )
            )
            for tree_index in self._tree_order:
                matches = initial_matches[tree_index]
                candidates = (
                    matches if candidates is None else candidates & matches
                )
                if len(candidates) <= self._PROJECTION_REFINEMENT_LIMIT:
                    break
        else:
            for tree_index in self._tree_order:
                _raise_if_cancelled(cancelled)
                matches = set(
                    self._trees[tree_index].query_ball_point(
                        self._matrices[tree_index][item_index],
                        r=self._maximum_distance,
                        p=1,
                        workers=4,
                    )
                )
                self.last_candidates_examined += len(matches)
                candidates = (
                    matches if candidates is None else candidates & matches
                )
                if len(candidates) <= self._PROJECTION_REFINEMENT_LIMIT:
                    break
        return self._exact_matches(item_index, candidates or ())

    def search_many(
        self,
        item_indexes: tuple[int, ...],
        cancelled: Callable[[], bool] | None,
    ) -> dict[int, tuple[int, ...]]:
        """批量按选择性查询投影，逐项取交集后执行原始 RGBA 精确过滤。"""
        if not item_indexes:
            return {}
        self.last_candidates_examined = 0
        candidates_by_item: dict[int, set[int] | None] = {
            item_index: None for item_index in item_indexes
        }
        active_indexes = item_indexes
        tree_order = self._tree_order or tuple(range(len(self._trees)))
        for tree_index in tree_order:
            if not active_indexes:
                break
            _raise_if_cancelled(cancelled)
            batch_matches = self._trees[tree_index].query_ball_point(
                self._matrices[tree_index][list(active_indexes)],
                r=self._maximum_distance,
                p=1,
                workers=4,
            )
            next_active = []
            for item_index, matches in zip(active_indexes, batch_matches):
                self.last_candidates_examined += len(matches)
                current = candidates_by_item[item_index]
                match_set = set(matches)
                candidates_by_item[item_index] = (
                    match_set if current is None else current & match_set
                )
                if (
                    len(candidates_by_item[item_index])
                    > self._PROJECTION_REFINEMENT_LIMIT
                ):
                    next_active.append(item_index)
            active_indexes = tuple(next_active)
        _raise_if_cancelled(cancelled)
        return {
            item_index: self._exact_matches(
                item_index,
                candidates_by_item[item_index] or (),
            )
            for item_index in item_indexes
        }


class _GrayscaleCandidateIndex:
    """先按灰度、再按颜色 L1 距离过滤密集双哈希碰撞桶。"""

    _BATCH_SIZE = 64
    _COLOR_FILTER_LIMIT = 32
    _STRICT_LOWER_BOUND_BLOCKS = 32
    _WIDE_LOWER_BOUND_BLOCKS = 64
    _STRICT_RGBA_LOWER_BOUND_BLOCKS = 32
    _WIDE_RGBA_LOWER_BOUND_BLOCKS = 64
    _WIDE_RADIUS_RATIO = 0.08

    def __init__(
        self,
        clusters: tuple[_FingerprintCluster, ...],
        maximum_distance: float,
        cancelled: Callable[[], bool] | None,
        maximum_rgba_distance: float | None = None,
    ):
        """建立灰度索引；RGBA 索引仅在密集灰度邻域首次出现时创建。"""
        grayscale_length = len(clusters[0].representative.grayscale)
        raw_grayscale = b"".join(
            cluster.representative.grayscale for cluster in clusters
        )
        grayscale_matrix = np.frombuffer(raw_grayscale, dtype=np.uint8).reshape(
            len(clusters),
            grayscale_length,
        )
        maximum_possible_distance = 255 * grayscale_length
        block_count = (
            self._WIDE_LOWER_BOUND_BLOCKS
            if maximum_distance
            > maximum_possible_distance * self._WIDE_RADIUS_RATIO
            else self._STRICT_LOWER_BOUND_BLOCKS
        )
        self._grayscale_index = _ByteL1CandidateIndex(
            grayscale_matrix,
            maximum_distance,
            block_count,
            cancelled,
        )

        rgba_descriptors = tuple(
            cluster.representative.rgba for cluster in clusters
        )
        rgba_lengths = {len(descriptor) for descriptor in rgba_descriptors}
        self._rgba_matrix: np.ndarray | None = None
        if (
            maximum_rgba_distance is not None
            and len(rgba_lengths) == 1
            and 0 not in rgba_lengths
        ):
            rgba_length = len(rgba_descriptors[0])
            self._rgba_matrix = np.frombuffer(
                b"".join(rgba_descriptors),
                dtype=np.uint8,
            ).reshape(len(clusters), rgba_length)
        self._maximum_rgba_distance = maximum_rgba_distance
        self._rgba_index: _RgbaL1CandidateIndex | None = None
        self._prefer_rgba = False
        self.last_candidates_examined = 0
        self.last_projection_candidates_examined = 0

    def _copy_rgba_metrics(self, rgba_index: _RgbaL1CandidateIndex) -> None:
        """把最近一次颜色查询的内部工作量暴露给分组指标。"""
        self.last_projection_candidates_examined = (
            rgba_index.last_candidates_examined
        )

    def _ensure_rgba_index(
        self,
        cancelled: Callable[[], bool] | None,
    ) -> _RgbaL1CandidateIndex | None:
        """需要过滤密集灰度桶时才付出颜色索引的内存和建树成本。"""
        if self._rgba_matrix is None or self._maximum_rgba_distance is None:
            return None
        if self._rgba_index is None:
            maximum_possible_distance = 255 * self._rgba_matrix.shape[1]
            block_count = (
                self._WIDE_RGBA_LOWER_BOUND_BLOCKS
                if self._maximum_rgba_distance
                > maximum_possible_distance * self._WIDE_RADIUS_RATIO
                else self._STRICT_RGBA_LOWER_BOUND_BLOCKS
            )
            self._rgba_index = _RgbaL1CandidateIndex(
                self._rgba_matrix,
                self._maximum_rgba_distance,
                block_count,
                cancelled,
            )
        return self._rgba_index

    def _filter_dense_by_rgba(
        self,
        item_index: int,
        grayscale_matches: tuple[int, ...],
        cancelled: Callable[[], bool] | None,
    ) -> tuple[int, ...]:
        """颜色索引与灰度邻域取交集，避免颜色拒绝路径平方级退化。"""
        if len(grayscale_matches) <= self._COLOR_FILTER_LIMIT:
            return grayscale_matches
        rgba_index = self._ensure_rgba_index(cancelled)
        if rgba_index is None:
            return grayscale_matches
        rgba_matches_tuple = rgba_index.search(item_index, cancelled)
        self._copy_rgba_metrics(rgba_index)
        if len(rgba_matches_tuple) < len(grayscale_matches):
            self._prefer_rgba = True
        rgba_matches = set(rgba_matches_tuple)
        return tuple(
            index for index in grayscale_matches if index in rgba_matches
        )

    def search(
        self,
        item_index: int,
        cancelled: Callable[[], bool] | None,
    ) -> tuple[int, ...]:
        """返回同时落入灰度及可用颜色阈值的候选。"""
        rgba_index = self._ensure_rgba_index(cancelled) if self._prefer_rgba else None
        if rgba_index is not None:
            rgba_matches = rgba_index.search(item_index, cancelled)
            self._copy_rgba_metrics(rgba_index)
            matches = self._grayscale_index._exact_matches(
                item_index,
                rgba_matches,
            )
            self.last_candidates_examined = len(matches)
            return matches
        grayscale_matches = self._grayscale_index.search(item_index, cancelled)
        self.last_projection_candidates_examined = 0
        matches = self._filter_dense_by_rgba(
            item_index,
            grayscale_matches,
            cancelled,
        )
        self.last_candidates_examined = len(matches)
        return matches

    def search_many(
        self,
        item_indexes: tuple[int, ...],
        cancelled: Callable[[], bool] | None,
    ) -> dict[int, tuple[int, ...]]:
        """批量查询灰度邻域，并对其中的密集邻域统一执行颜色过滤。"""
        bounded_indexes = item_indexes[: self._BATCH_SIZE]
        self.last_projection_candidates_examined = 0
        rgba_index = self._ensure_rgba_index(cancelled) if self._prefer_rgba else None
        if rgba_index is not None:
            rgba_matches = rgba_index.search_many(bounded_indexes, cancelled)
            self._copy_rgba_metrics(rgba_index)
            return {
                item_index: self._grayscale_index._exact_matches(
                    item_index,
                    rgba_matches.get(item_index, ()),
                )
                for item_index in bounded_indexes
            }
        grayscale_matches = self._grayscale_index.search_many(
            bounded_indexes,
            cancelled,
        )
        dense_indexes = tuple(
            item_index
            for item_index in bounded_indexes
            if len(grayscale_matches.get(item_index, ()))
            > self._COLOR_FILTER_LIMIT
        )
        rgba_index = (
            self._ensure_rgba_index(cancelled) if dense_indexes else None
        )
        if rgba_index is None:
            return grayscale_matches
        rgba_matches = rgba_index.search_many(dense_indexes, cancelled)
        self._copy_rgba_metrics(rgba_index)
        for item_index in dense_indexes:
            color_candidates = set(rgba_matches.get(item_index, ()))
            grayscale_matches[item_index] = tuple(
                index
                for index in grayscale_matches[item_index]
                if index in color_candidates
            )
        return grayscale_matches


def _quality_key(fingerprint: ImageFingerprint) -> tuple[int, int, str, int]:
    """生成建议保留排序键：像素、大小、路径、枚举序号。"""
    record = fingerprint.record
    return (
        -record.pixel_count,
        -record.size_bytes,
        str(record.canonical_path).casefold(),
        record.order,
    )


def select_suggested_keep(
    members: tuple[ImageFingerprint, ...] | list[ImageFingerprint],
) -> ImageFingerprint:
    """按稳定质量顺序选择一张建议保留图片。"""
    if not members:
        raise ValueError("相似组至少需要一个成员")
    return min(members, key=_quality_key)


def is_visually_similar(
    first: ImageFingerprint,
    second: ImageFingerprint,
    thresholds: SimilarityThresholds,
) -> bool:
    """要求候选同时通过两种哈希、宽高比和灰度复核。"""
    if hamming_distance(first.phash, second.phash) > thresholds.phash_distance:
        return False
    if hamming_distance(first.dhash, second.dhash) > thresholds.dhash_distance:
        return False
    if (
        aspect_ratio_difference(first.record, second.record)
        > thresholds.aspect_ratio_difference
    ):
        return False
    if (
        grayscale_similarity(first.grayscale, second.grayscale)
        < thresholds.grayscale_similarity
    ):
        return False
    if first.rgba and second.rgba:
        return (
            grayscale_similarity(first.rgba, second.rgba)
            >= thresholds.grayscale_similarity
        )
    return True


def _build_exact_clusters(
    fingerprints: tuple[ImageFingerprint, ...],
    cancelled: Callable[[], bool] | None = None,
) -> tuple[_FingerprintCluster, ...]:
    """把同 SHA-256 成员收缩，同时让无哈希成员保持独立。"""
    buckets: dict[tuple[str, object], list[ImageFingerprint]] = {}
    for fingerprint in sorted(fingerprints, key=lambda item: item.record.order):
        _raise_if_cancelled(cancelled)
        if fingerprint.sha256 is None:
            key: tuple[str, object] = ("single", fingerprint.record.order)
        else:
            key = ("sha256", fingerprint.sha256)
        buckets.setdefault(key, []).append(fingerprint)

    clusters = []
    for members in buckets.values():
        _raise_if_cancelled(cancelled)
        ordered_members = tuple(sorted(members, key=lambda item: item.record.order))
        clusters.append(
            _FingerprintCluster(
                members=ordered_members,
                representative=select_suggested_keep(ordered_members),
            )
        )
    return tuple(clusters)


def _group_type_for(members: tuple[ImageFingerprint, ...]) -> GroupType:
    """仅在全部成员具有同一真实 SHA-256 时标记完全重复。"""
    hashes = {member.sha256 for member in members}
    if None not in hashes and len(hashes) == 1:
        return GroupType.EXACT
    return GroupType.VISUAL


def build_similarity_groups(
    fingerprints: tuple[ImageFingerprint, ...] | list[ImageFingerprint],
    preset: SimilarityPreset,
    *,
    metrics: GroupingMetrics | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[SimilarityGroup, ...]:
    """通过代表图直接匹配生成稳定且不传递扩张的相似组。"""
    _raise_if_cancelled(cancelled)
    normalized = tuple(fingerprints)
    if len(normalized) < 2:
        return ()

    thresholds = thresholds_for(preset)
    clusters = _build_exact_clusters(normalized, cancelled)
    phash_tree = BKTree()
    dhash_tree = BKTree()
    phash_counts: dict[int, int] = {}
    dhash_counts: dict[int, int] = {}
    for index, cluster in enumerate(clusters):
        _raise_if_cancelled(cancelled)
        representative = cluster.representative
        phash_tree.add(representative.phash, index)
        dhash_tree.add(representative.dhash, index)
        phash_counts[representative.phash] = (
            phash_counts.get(representative.phash, 0) + 1
        )
        dhash_counts[representative.dhash] = (
            dhash_counts.get(representative.dhash, 0) + 1
        )

    collision_limit = 32
    grayscale_index: _GrayscaleCandidateIndex | None = None
    maximum_gray_distance = math.ceil(
        (1.0 - thresholds.grayscale_similarity)
        * 255
        * len(clusters[0].representative.grayscale)
    )
    maximum_rgba_distance = math.ceil(
        (1.0 - thresholds.grayscale_similarity)
        * 255
        * len(clusters[0].representative.rgba)
    )
    grayscale_preferred_indexes: set[int] = set()

    seed_indexes = sorted(
        range(len(clusters)),
        key=lambda index: _quality_key(clusters[index].representative),
    )
    assigned: set[int] = set()
    pending_groups: list[tuple[ImageFingerprint, ...]] = []
    grayscale_candidate_cache: dict[int, tuple[int, ...]] = {}
    grayscale_batching_enabled = False

    def grayscale_candidates_for(
        seed_index: int,
        seed_position: int,
    ) -> tuple[int, ...]:
        """惰性取得当前种子的灰度邻域，并只为稀疏邻域做有界预取。"""
        nonlocal grayscale_index, grayscale_batching_enabled
        if grayscale_index is None:
            grayscale_index = _GrayscaleCandidateIndex(
                clusters,
                maximum_gray_distance,
                cancelled,
                maximum_rgba_distance,
            )
        cached = grayscale_candidate_cache.pop(seed_index, None)
        if cached is not None:
            candidates = cached
        elif grayscale_batching_enabled:
            pending_indexes = tuple(
                index
                for index in seed_indexes[seed_position:]
                if index not in assigned
            )[: _GrayscaleCandidateIndex._BATCH_SIZE]
            grayscale_candidate_cache.update(
                grayscale_index.search_many(pending_indexes, cancelled)
            )
            if metrics is not None:
                metrics.projection_candidates_examined += (
                    grayscale_index.last_projection_candidates_examined
                )
            candidates = grayscale_candidate_cache.pop(seed_index, ())
        else:
            candidates = grayscale_index.search(seed_index, cancelled)
            if metrics is not None:
                metrics.projection_candidates_examined += (
                    grayscale_index.last_projection_candidates_examined
                )
        if len(candidates) <= collision_limit:
            grayscale_batching_enabled = True
        else:
            grayscale_batching_enabled = False
            grayscale_candidate_cache.clear()
        return candidates

    for seed_position, seed_index in enumerate(seed_indexes):
        _raise_if_cancelled(cancelled)
        if seed_index in assigned:
            continue
        seed = clusters[seed_index]
        seed_representative = seed.representative
        # 灰度索引偏好只传播给同时落入双哈希密集邻域的项目，避免单侧碰撞污染。
        use_grayscale_index = seed_index in grayscale_preferred_indexes
        use_phash_index = (
            phash_counts[seed_representative.phash]
            <= dhash_counts[seed_representative.dhash]
        )
        candidates_have_both_hashes = False
        if use_grayscale_index:
            indexed_candidates = grayscale_candidates_for(
                seed_index,
                seed_position,
            )
            if metrics is not None:
                metrics.index_candidates_examined += len(indexed_candidates)
        else:
            selected_tree = phash_tree if use_phash_index else dhash_tree
            selected_value = (
                seed_representative.phash
                if use_phash_index
                else seed_representative.dhash
            )
            selected_radius = (
                thresholds.phash_distance
                if use_phash_index
                else thresholds.dhash_distance
            )
            indexed_candidates = selected_tree.search(
                selected_value,
                selected_radius,
                cancelled,
                maximum_results=collision_limit + 1,
            )
            if metrics is not None:
                metrics.index_candidates_examined += len(indexed_candidates)

            if len(indexed_candidates) > collision_limit:
                alternate_tree = dhash_tree if use_phash_index else phash_tree
                alternate_value = (
                    seed_representative.dhash
                    if use_phash_index
                    else seed_representative.phash
                )
                alternate_radius = (
                    thresholds.dhash_distance
                    if use_phash_index
                    else thresholds.phash_distance
                )
                alternate_candidates = alternate_tree.search(
                    alternate_value,
                    alternate_radius,
                    cancelled,
                    maximum_results=collision_limit + 1,
                )
                if metrics is not None:
                    metrics.index_candidates_examined += len(alternate_candidates)

                if len(alternate_candidates) <= collision_limit:
                    indexed_candidates = alternate_candidates
                    use_phash_index = not use_phash_index
                else:
                    selected_candidates = selected_tree.search(
                        selected_value,
                        selected_radius,
                        cancelled,
                    )
                    if metrics is not None:
                        metrics.index_candidates_examined += len(
                            selected_candidates
                        )
                    joint_candidates = []
                    for candidate_index in selected_candidates:
                        _raise_if_cancelled(cancelled)
                        candidate_representative = clusters[
                            candidate_index
                        ].representative
                        if metrics is not None:
                            metrics.hash_filter_checks += 1
                        alternate_hash = (
                            candidate_representative.dhash
                            if use_phash_index
                            else candidate_representative.phash
                        )
                        if hamming_distance(
                            alternate_value,
                            alternate_hash,
                        ) <= alternate_radius:
                            joint_candidates.append(candidate_index)
                    indexed_candidates = tuple(joint_candidates)
                    candidates_have_both_hashes = True

                    if len(indexed_candidates) > collision_limit:
                        grayscale_candidates = grayscale_candidates_for(
                            seed_index,
                            seed_position,
                        )
                        if metrics is not None:
                            metrics.index_candidates_examined += len(
                                grayscale_candidates
                            )
                        if len(grayscale_candidates) < len(indexed_candidates):
                            for candidate_index in indexed_candidates:
                                core_representative = clusters[
                                    candidate_index
                                ].representative
                                if (
                                    hamming_distance(
                                        seed_representative.phash,
                                        core_representative.phash,
                                    )
                                    <= thresholds.phash_distance // 2
                                    and hamming_distance(
                                        seed_representative.dhash,
                                        core_representative.dhash,
                                    )
                                    <= thresholds.dhash_distance // 2
                                ):
                                    grayscale_preferred_indexes.add(
                                        candidate_index
                                    )
                            use_grayscale_index = True
                            candidates_have_both_hashes = False
                            indexed_candidates = grayscale_candidates
        accepted = [seed_index]
        for candidate_index in indexed_candidates:
            _raise_if_cancelled(cancelled)
            if candidate_index == seed_index or candidate_index in assigned:
                continue
            candidate = clusters[candidate_index]
            candidate_representative = candidate.representative
            if not candidates_have_both_hashes and metrics is not None:
                metrics.hash_filter_checks += 1
            if not candidates_have_both_hashes and use_grayscale_index:
                if (
                    hamming_distance(
                        seed_representative.phash,
                        candidate_representative.phash,
                    )
                    > thresholds.phash_distance
                    or hamming_distance(
                        seed_representative.dhash,
                        candidate_representative.dhash,
                    )
                    > thresholds.dhash_distance
                ):
                    continue
            elif not candidates_have_both_hashes and use_phash_index:
                if (
                    hamming_distance(
                        seed_representative.dhash,
                        candidate_representative.dhash,
                    )
                    > thresholds.dhash_distance
                ):
                    continue
            elif not candidates_have_both_hashes and (
                hamming_distance(
                    seed_representative.phash,
                    candidate_representative.phash,
                )
                > thresholds.phash_distance
            ):
                continue
            if metrics is not None:
                metrics.candidate_rechecks += 1
            if is_visually_similar(
                seed_representative,
                candidate_representative,
                thresholds,
            ):
                accepted.append(candidate_index)

        members = tuple(
            sorted(
                (
                    member
                    for cluster_index in accepted
                    for member in clusters[cluster_index].members
                ),
                key=lambda item: item.record.order,
            )
        )
        if len(members) < 2:
            continue
        assigned.update(accepted)
        pending_groups.append(members)

    groups = []
    for group_number, members in enumerate(pending_groups, start=1):
        _raise_if_cancelled(cancelled)
        suggested = select_suggested_keep(members)
        groups.append(
            SimilarityGroup(
                group_id=f"group-{group_number:05d}",
                group_type=_group_type_for(members),
                representative=suggested,
                members=members,
                suggested_keep=suggested,
            )
        )
    return tuple(groups)
