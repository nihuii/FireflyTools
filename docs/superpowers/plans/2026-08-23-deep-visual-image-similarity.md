# 图片相似度深度视觉特征 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有图片相似度工具中加入本地 DINOv2 全局与局部视觉特征，使轻微姿态变化但动作、表情和构图高度一致的图片进入严格档，同时保持可解释分档、离线隐私和回收站安全边界。

**Architecture:** 保留 SHA-256、pHash、dHash、灰度和 RGBA 经典分支，新增可注入的 ONNX 深度特征后端、版本化全局缓存、近邻候选索引、Patch 空间一致性和证据融合。扫描器生成经典与深度候选并把已接受的标量证据交给现有代表图直接分组；UI 负责模型安装选择、阶段进度、关系解释和宽松档风险提示，模型二进制及用户图片均不进入 Git。

**Tech Stack:** Python 3.13、PyQt6、Pillow、ImageHash、NumPy、SciPy、ONNX Runtime CPU、requests、sqlite3、unittest

---

## 实施约束

- 在专用工作树或专用分支执行，不在含用户未提交改动的工作树混合实现。
- `pic_test/01567-1273093529.png` 与 `pic_test/01569-1273093531.png` 只用于本地验收，不暂存、不修改、不删除。
- 不删除 `tests/tmpbs_9xr3j`、`tests/tmpig9jv4e5`、`tests/tmpn0jwtglu`、`tests/tmpywtu22b5` 或其他来源不明文件。
- 所有运行时单元测试必须断网可运行，不能自动下载模型。
- 模型发布、Git 推送和远端 Release 资产上传分别需要明确授权；实现提交不隐含这些权限。
- 每个任务先写失败测试，再写最小实现；只暂存任务列出的文件。
- 完整扫描结果继续默认零选择；任何深度结果都不能绕过 `RecycleBinService` 的文件快照复核。

## 文件结构锁定

### 新增运行时模块

- `tools/image_similarity/deep_models.py`：深度模型规格、状态、阈值、描述符和证据类型，并重导出公共扫描模式与关系枚举。
- `tools/image_similarity/deep_features.py`：Pillow 预处理、ONNX 会话、全局与 Patch 特征提取。
- `tools/image_similarity/model_manager.py`：模型状态、HTTPS 下载、SHA-256 校验和原子安装。
- `tools/image_similarity/deep_feature_cache.py`：以内容哈希和模型版本为键的 SQLite 全局特征缓存。
- `tools/image_similarity/deep_index.py`：经典救援候选和全局特征随机投影近邻索引。
- `tools/image_similarity/deep_matching.py`：Patch 双向匹配、空间一致性、经典佐证和档位融合。

### 新增开发与校准文件

- `scripts/export_dinov2_onnx.py`：从固定 DINOv2 ViT-S/14 权重导出双输出 ONNX。
- `scripts/verify_dinov2_onnx.py`：比较 PyTorch 与 ONNX 全局/Patch 输出。
- `scripts/calibrate_deep_similarity.py`：读取标注图片对、计算阈值并写版本化配置。
- `requirements-model-export.txt`：模型导出专用依赖，不进入桌面运行时依赖。
- `tools/image_similarity/assets/dinov2_vits14.json`：由校验脚本生成的模型清单。
- `tools/image_similarity/assets/dinov2_vits14_thresholds.json`：由校准脚本生成的三档阈值。

### 修改现有模块

- `tools/image_similarity/models.py:40-179`：扩展扫描阶段、错误码、组关系证据和结果模式。
- `tools/image_similarity/fingerprints.py:17-145`：增加裁剪鲁棒多段哈希。
- `tools/image_similarity/grouping.py:596-961`：接收深度证据候选，保留经典分支和代表图直接匹配。
- `tools/image_similarity/scanner.py:193-541`：智能模式完整哈希、缓存、全局特征、候选与局部复核。
- `tools/image_similarity_tool.py:52-84,405-664,738-797,798-1245`：模型安装、扫描模式、关系标签、证据摘要和风险交互。
- `tools/image_similarity/__init__.py`：导出稳定公共枚举和数据类型。
- `requirements.txt`：增加 Windows/Python 3.13 验证过的 ONNX Runtime CPU 依赖范围。

### 新增测试

- `tests/test_image_similarity_deep_models.py`
- `tests/test_image_similarity_deep_features.py`
- `tests/test_image_similarity_model_manager.py`
- `tests/test_image_similarity_deep_feature_cache.py`
- `tests/test_image_similarity_deep_index.py`
- `tests/test_image_similarity_deep_matching.py`

### 修改测试

- `tests/test_image_similarity_fingerprints.py`
- `tests/test_image_similarity_grouping.py`
- `tests/test_image_similarity_scanner.py`
- `tests/test_image_similarity_tool.py`
- `tests/test_image_similarity_recycle_bin.py`
- `tests/test_code_documentation.py`

## Task 1：深度数据契约、扫描模式与裁剪鲁棒指纹

**Files:**

- Create: `tools/image_similarity/deep_models.py`
- Create: `tests/test_image_similarity_deep_models.py`
- Modify: `tools/image_similarity/models.py:40-179`
- Modify: `tools/image_similarity/fingerprints.py:17-145`
- Modify: `tools/image_similarity/__init__.py`
- Modify: `tests/test_image_similarity_fingerprints.py`

- [ ] **Step 1：写失败测试，锁定不可变类型、三档单调性和裁剪哈希**

在 `tests/test_image_similarity_deep_models.py` 写入：

```python
import unittest

from tools.image_similarity.deep_models import (
    ClassicCorroboration,
    DeepSimilarityThresholds,
    ScanMode,
    SimilarityEvidence,
    SimilarityRelation,
    validate_threshold_order,
)
from tools.image_similarity.models import SimilarityPreset


class DeepModelTests(unittest.TestCase):
    def test_thresholds_must_be_monotonic(self):
        thresholds = {
            SimilarityPreset.STRICT: DeepSimilarityThresholds(0.90, 0.70, 0.75, 0.60),
            SimilarityPreset.STANDARD: DeepSimilarityThresholds(0.82, 0.55, 0.55, 0.50),
            SimilarityPreset.LOOSE: DeepSimilarityThresholds(0.72, 0.30, 0.00, 0.40),
        }
        validate_threshold_order(thresholds)

    def test_reversed_global_threshold_is_rejected(self):
        thresholds = {
            SimilarityPreset.STRICT: DeepSimilarityThresholds(0.80, 0.70, 0.75, 0.60),
            SimilarityPreset.STANDARD: DeepSimilarityThresholds(0.82, 0.55, 0.55, 0.50),
            SimilarityPreset.LOOSE: DeepSimilarityThresholds(0.72, 0.30, 0.00, 0.40),
        }
        with self.assertRaisesRegex(ValueError, "严格档"):
            validate_threshold_order(thresholds)

    def test_similarity_evidence_keeps_only_scalar_runtime_data(self):
        evidence = SimilarityEvidence(
            first_order=1,
            second_order=2,
            global_similarity=0.91,
            patch_coverage=0.72,
            spatial_consistency=0.80,
            mean_patch_similarity=0.83,
            corroborations=(ClassicCorroboration.ASPECT_RATIO,),
            relation=SimilarityRelation.INTELLIGENT_HIGH,
        )
        self.assertEqual((1, 2), evidence.pair_key)
        self.assertEqual(ScanMode.INTELLIGENT, ScanMode("intelligent"))
```

在 `tests/test_image_similarity_fingerprints.py` 增加：

```python
def test_crop_resistant_segments_survive_small_crop(self):
    original = self._pattern_image((160, 120))
    cropped = original.crop((8, 6, 152, 114)).resize((160, 120))
    original.save(self.root / "original.png")
    cropped.save(self.root / "cropped.png")
    first = fingerprint_image(self.root / "original.png", 0)
    second = fingerprint_image(self.root / "cropped.png", 1)
    self.assertTrue(first.crop_hash_segments)
    self.assertTrue(second.crop_hash_segments)
    self.assertTrue(
        any(
            hamming_distance(left, right) <= 16
            for left in first.crop_hash_segments
            for right in second.crop_hash_segments
        )
    )
```

- [ ] **Step 2：运行测试并确认类型与字段缺失**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_image_similarity_deep_models tests.test_image_similarity_fingerprints -v
```

Expected: `tools.image_similarity.deep_models` 导入失败，或 `ImageFingerprint` 缺少 `crop_hash_segments`。

- [ ] **Step 3：实现深度公共契约并以默认字段保持现有构造兼容**

`deep_models.py` 定义以下稳定接口：

```python
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import numpy as np

from tools.image_similarity.models import ScanMode, SimilarityPreset, SimilarityRelation


class DeepModelStatus(Enum):
    NOT_INSTALLED = "not_installed"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    READY = "ready"
    CORRUPT = "corrupt"
    INCOMPATIBLE = "incompatible"


class ClassicCorroboration(Enum):
    ASPECT_RATIO = "aspect_ratio"
    GRAYSCALE = "grayscale"
    RGBA = "rgba"
    PHASH_CANDIDATE = "phash_candidate"
    DHASH_CANDIDATE = "dhash_candidate"
    CROP_HASH = "crop_hash"


@dataclass(frozen=True)
class DeepModelSpec:
    model_id: str
    version: str
    download_url: str
    sha256: str
    size_bytes: int
    input_size: int
    feature_dimension: int
    patch_rows: int
    patch_columns: int
    preprocessing_version: str
    input_name: str
    global_output_name: str
    patch_output_name: str
    license_url: str


@dataclass(frozen=True)
class DeepSimilarityThresholds:
    global_similarity: float
    patch_coverage: float
    spatial_consistency: float
    patch_similarity: float


@dataclass(frozen=True)
class DeepGlobalDescriptor:
    content_sha256: str
    model_key: str
    vector: np.ndarray


@dataclass(frozen=True)
class DeepPatchDescriptor:
    vectors: np.ndarray
    rows: int
    columns: int


@dataclass(frozen=True)
class SimilarityEvidence:
    first_order: int
    second_order: int
    global_similarity: float | None
    patch_coverage: float | None
    spatial_consistency: float | None
    mean_patch_similarity: float | None
    corroborations: tuple[ClassicCorroboration, ...]
    relation: SimilarityRelation

    @property
    def pair_key(self) -> tuple[int, int]:
        return (
            min(self.first_order, self.second_order),
            max(self.first_order, self.second_order),
        )


def validate_threshold_order(
    thresholds: Mapping[SimilarityPreset, DeepSimilarityThresholds],
) -> None:
    strict = thresholds[SimilarityPreset.STRICT]
    standard = thresholds[SimilarityPreset.STANDARD]
    loose = thresholds[SimilarityPreset.LOOSE]
    fields = (
        ("全局", strict.global_similarity, standard.global_similarity, loose.global_similarity),
        ("局部", strict.patch_coverage, standard.patch_coverage, loose.patch_coverage),
        ("空间", strict.spatial_consistency, standard.spatial_consistency, loose.spatial_consistency),
        ("Patch", strict.patch_similarity, standard.patch_similarity, loose.patch_similarity),
    )
    for label, strict_value, standard_value, loose_value in fields:
        if not strict_value >= standard_value >= loose_value:
            raise ValueError(f"严格档、标准档、宽松档的{label}阈值必须单调")


def freeze_thresholds(
    thresholds: Mapping[SimilarityPreset, DeepSimilarityThresholds],
) -> Mapping[SimilarityPreset, DeepSimilarityThresholds]:
    validate_threshold_order(thresholds)
    return MappingProxyType(dict(thresholds))
```

`ScanMode` 和 `SimilarityRelation` 放入 `models.py`，使 `SimilarityGroup` 与 `ScanResult` 可以在运行时引用枚举；`deep_models.py` 从 `models.py` 导入并重导出它们，避免两个模块循环导入。`models.py` 做向后兼容扩展：

```python
class ScanMode(Enum):
    CLASSIC_ONLY = "classic_only"
    INTELLIGENT = "intelligent"


class SimilarityRelation(Enum):
    EXACT = "exact"
    CLASSIC_NEAR_DUPLICATE = "classic_near_duplicate"
    INTELLIGENT_HIGH = "intelligent_high"
    INTELLIGENT_RELATED = "intelligent_related"


class ScanPhase(Enum):
    ENUMERATING = "enumerating"
    FINGERPRINTING = "fingerprinting"
    HASHING = "hashing"
    DEEP_GLOBAL = "deep_global"
    CANDIDATES = "candidates"
    DEEP_LOCAL = "deep_local"
    GROUPING = "grouping"
    THUMBNAILS = "thumbnails"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ImageFingerprint:
    record: ImageRecord
    phash: int
    dhash: int
    grayscale: bytes
    rgba: bytes = b""
    crop_hash_segments: tuple[int, ...] = ()
    sha256: str | None = None


@dataclass(frozen=True)
class SimilarityGroup:
    group_id: str
    group_type: GroupType
    representative: ImageFingerprint
    members: tuple[ImageFingerprint, ...]
    suggested_keep: ImageFingerprint
    relation: SimilarityRelation = SimilarityRelation.CLASSIC_NEAR_DUPLICATE
    evidence: tuple[SimilarityEvidence, ...] = ()


@dataclass(frozen=True)
class ScanResult:
    root: Path
    preset: SimilarityPreset
    groups: tuple[SimilarityGroup, ...]
    fingerprints: tuple[ImageFingerprint, ...]
    failures: tuple[ScanFailure, ...]
    cancelled: bool = False
    scan_mode: ScanMode = ScanMode.CLASSIC_ONLY
    deep_evidence: tuple[SimilarityEvidence, ...] = ()
```

在 `SimilarityGroup` 增加 `relation` 与 `evidence` 默认字段，在 `ScanResult` 增加 `scan_mode` 与 `deep_evidence` 默认字段；默认值必须让全部现有测试构造继续有效。文件顶部启用 `from __future__ import annotations`，`SimilarityEvidence` 只在 `TYPE_CHECKING` 分支导入，运行时默认值使用空 tuple，避免循环导入。

`fingerprints.py` 在同一次解码中加入：

```python
crop_hash = imagehash.crop_resistant_hash(rgb_image)
crop_hash_segments = tuple(
    int(str(segment_hash), 16)
    for segment_hash in crop_hash.segment_hashes
)
```

并把 `crop_hash_segments` 写入 `ImageFingerprint`。不要为了裁剪哈希再次打开图片。

- [ ] **Step 4：运行模型与指纹测试**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_image_similarity_deep_models tests.test_image_similarity_fingerprints tests.test_image_similarity_grouping -v
```

Expected: all tests `OK`，旧分组构造保持兼容。

- [ ] **Step 5：提交数据契约**

```powershell
git add -- tools/image_similarity/deep_models.py tools/image_similarity/models.py tools/image_similarity/fingerprints.py tools/image_similarity/__init__.py tests/test_image_similarity_deep_models.py tests/test_image_similarity_fingerprints.py
git commit -m "feat(image-similarity): 增加深度视觉数据契约"
```

## Task 2：DINOv2 导出、预处理与 ONNX 后端

**Files:**

- Create: `tools/image_similarity/deep_features.py`
- Create: `scripts/export_dinov2_onnx.py`
- Create: `scripts/verify_dinov2_onnx.py`
- Create: `requirements-model-export.txt`
- Create: `tests/test_image_similarity_deep_features.py`
- Modify: `requirements.txt`

- [ ] **Step 1：写失败测试，锁定预处理、输出校验和惰性会话**

测试用假会话，不导入真实 `onnxruntime`：

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image

from tools.image_similarity.deep_features import OnnxDeepFeatureBackend
from tools.image_similarity.deep_models import DeepModelSpec


class FakeSession:
    def __init__(self):
        self.calls = []

    def run(self, output_names, inputs):
        self.calls.append((tuple(output_names), inputs))
        batch = next(iter(inputs.values())).shape[0]
        global_features = np.ones((batch, 384), dtype=np.float32)
        patch_features = np.ones((batch, 256, 384), dtype=np.float32)
        return global_features, patch_features


class DeepFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image_path = self.root / "sample.png"
        Image.new("RGB", (320, 240), (60, 120, 180)).save(self.image_path)
        self.spec = DeepModelSpec(
            model_id="dinov2-vits14",
            version="1",
            download_url="https://example.test/dinov2.onnx",
            sha256="0" * 64,
            size_bytes=1,
            input_size=224,
            feature_dimension=384,
            patch_rows=16,
            patch_columns=16,
            preprocessing_version="dinov2-imagenet-v1",
            input_name="pixel_values",
            global_output_name="global_features",
            patch_output_name="patch_features",
            license_url="https://www.apache.org/licenses/LICENSE-2.0",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_global_and_patch_outputs_are_l2_normalized(self):
        session = FakeSession()
        backend = OnnxDeepFeatureBackend(
            self.root / "model.onnx",
            self.spec,
            session_factory=lambda path: session,
        )
        global_descriptor = backend.extract_global(self.image_path, "a" * 64)
        patch_descriptor = backend.extract_patches(self.image_path)
        self.assertAlmostEqual(1.0, float(np.linalg.norm(global_descriptor.vector)), places=5)
        self.assertTrue(
            np.allclose(np.linalg.norm(patch_descriptor.vectors, axis=1), 1.0)
        )
        self.assertEqual((256, 384), patch_descriptor.vectors.shape)

    def test_non_finite_output_is_rejected(self):
        class InvalidSession(FakeSession):
            def run(self, output_names, inputs):
                outputs = list(super().run(output_names, inputs))
                outputs[0][0, 0] = np.nan
                return tuple(outputs)

        backend = OnnxDeepFeatureBackend(
            self.root / "model.onnx",
            self.spec,
            session_factory=lambda path: InvalidSession(),
        )
        with self.assertRaisesRegex(ValueError, "非有限值"):
            backend.extract_global(self.image_path, "a" * 64)
```

- [ ] **Step 2：运行测试确认后端模块缺失**

Run: `python -m unittest tests.test_image_similarity_deep_features -v`

Expected: import failure for `tools.image_similarity.deep_features`。

- [ ] **Step 3：实现固定预处理和可注入 ONNX 后端**

`deep_features.py` 必须提供：

```python
from pathlib import Path
from typing import Callable, Protocol
import warnings

import numpy as np
from PIL import Image, ImageOps

from tools.image_similarity.deep_models import (
    DeepGlobalDescriptor,
    DeepModelSpec,
    DeepPatchDescriptor,
)


class InferenceSession(Protocol):
    def run(self, output_names, inputs):
        raise NotImplementedError


def preprocess_dinov2(path: Path, input_size: int) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"Corrupt EXIF data\..*",
            category=UserWarning,
            module=r"PIL\.TiffImagePlugin",
        )
        with Image.open(path) as opened:
            normalized = ImageOps.exif_transpose(opened).convert("RGB")
            width, height = normalized.size
            scale = 256.0 / min(width, height)
            resized = normalized.resize(
                (round(width * scale), round(height * scale)),
                Image.Resampling.BICUBIC,
            )
            left = (resized.width - input_size) // 2
            top = (resized.height - input_size) // 2
            cropped = resized.crop((left, top, left + input_size, top + input_size))
            array = np.asarray(cropped, dtype=np.float32) / 255.0
    mean = np.asarray((0.485, 0.456, 0.406), dtype=np.float32)
    std = np.asarray((0.229, 0.224, 0.225), dtype=np.float32)
    chw = np.transpose((array - mean) / std, (2, 0, 1))
    return np.ascontiguousarray(chw[None, ...], dtype=np.float32)


def _normalize_rows(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float32)
    if not np.isfinite(array).all():
        raise ValueError("深度模型输出包含非有限值")
    norms = np.linalg.norm(array, axis=-1, keepdims=True)
    if np.any(norms <= 1e-12):
        raise ValueError("深度模型输出包含零向量")
    return np.ascontiguousarray(array / norms, dtype=np.float32)


class OnnxDeepFeatureBackend:
    def __init__(
        self,
        model_path: Path,
        spec: DeepModelSpec,
        session_factory: Callable[[Path], InferenceSession] | None = None,
    ):
        self.model_path = Path(model_path)
        self.spec = spec
        self._session_factory = session_factory or self._default_session_factory
        self._session = None

    @property
    def model_key(self) -> str:
        return f"{self.spec.model_id}:{self.spec.version}:{self.spec.preprocessing_version}"

    @staticmethod
    def _default_session_factory(model_path: Path):
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        return ort.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

    def _run(self, path: Path) -> tuple[np.ndarray, np.ndarray]:
        if self._session is None:
            self._session = self._session_factory(self.model_path)
        inputs = {self.spec.input_name: preprocess_dinov2(path, self.spec.input_size)}
        outputs = self._session.run(
            [self.spec.global_output_name, self.spec.patch_output_name],
            inputs,
        )
        global_features = np.asarray(outputs[0], dtype=np.float32)
        patch_features = np.asarray(outputs[1], dtype=np.float32)
        expected_patch_count = self.spec.patch_rows * self.spec.patch_columns
        if global_features.shape != (1, self.spec.feature_dimension):
            raise ValueError("深度模型全局输出维度不兼容")
        if patch_features.shape != (1, expected_patch_count, self.spec.feature_dimension):
            raise ValueError("深度模型局部输出维度不兼容")
        return global_features, patch_features

    def extract_global(self, path: Path, content_sha256: str) -> DeepGlobalDescriptor:
        global_features, _ = self._run(path)
        return DeepGlobalDescriptor(
            content_sha256=content_sha256,
            model_key=self.model_key,
            vector=_normalize_rows(global_features)[0],
        )

    def extract_patches(self, path: Path) -> DeepPatchDescriptor:
        _, patch_features = self._run(path)
        return DeepPatchDescriptor(
            vectors=_normalize_rows(patch_features[0]),
            rows=self.spec.patch_rows,
            columns=self.spec.patch_columns,
        )
```

实现时增加单次推理同时返回两类特征的内部批次 API，避免同一候选图因先取全局、后取 Patch 而重复推理；公开接口保持上述可测试行为。

`requirements.txt` 增加 `onnxruntime>=1.23,<2`。`requirements-model-export.txt` 固定 `torch>=2.8,<3` 与 `onnx>=1.19,<2`；执行前用当前 Python 3.13 环境实际安装验证，若无兼容 wheel，导出脚本改用隔离的 Python 3.12 环境，运行时仍保持 Python 3.13。

导出脚本使用固定仓库名 `facebookresearch/dinov2` 和模型名 `dinov2_vits14`，包装 `forward_features()` 的 `x_norm_clstoken` 与 `x_norm_patchtokens`，以 opset 17 导出名为 `pixel_values`、`global_features`、`patch_features` 的模型。验证脚本使用固定随机种子和一张本地生成 RGB 图，要求两端形状完全一致，归一化后全局余弦误差不超过 `1e-4`，Patch 最大绝对误差不超过 `1e-3`；不满足即退出非零状态。

- [ ] **Step 4：运行断网后端测试和静态编译**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_image_similarity_deep_features -v
python -m compileall -q tools/image_similarity scripts/export_dinov2_onnx.py scripts/verify_dinov2_onnx.py
```

Expected: tests `OK`；编译命令无输出。不要在该步骤下载权重。

- [ ] **Step 5：提交 ONNX 后端与导出工具**

```powershell
git add -- requirements.txt requirements-model-export.txt tools/image_similarity/deep_features.py scripts/export_dinov2_onnx.py scripts/verify_dinov2_onnx.py tests/test_image_similarity_deep_features.py
git commit -m "feat(image-similarity): 增加 DINOv2 ONNX 特征后端"
```

## Task 3：模型清单、下载确认后端与原子安装

**Files:**

- Create: `tools/image_similarity/model_manager.py`
- Create: `tests/test_image_similarity_model_manager.py`

- [ ] **Step 1：写失败测试覆盖未安装、校验、取消和损坏下载**

使用内存响应替身，不访问网络：

```python
import hashlib
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.image_similarity.deep_models import DeepModelSpec, DeepModelStatus
from tools.image_similarity.model_manager import DeepModelManager


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset:offset + chunk_size]

    def close(self):
        return None


class ModelManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.payload = b"verified model bytes"
        self.spec = DeepModelSpec(
            model_id="dinov2-vits14",
            version="1",
            download_url="https://example.test/model.onnx",
            sha256=hashlib.sha256(self.payload).hexdigest(),
            size_bytes=len(self.payload),
            input_size=224,
            feature_dimension=384,
            patch_rows=16,
            patch_columns=16,
            preprocessing_version="dinov2-imagenet-v1",
            input_name="pixel_values",
            global_output_name="global_features",
            patch_output_name="patch_features",
            license_url="https://www.apache.org/licenses/LICENSE-2.0",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_install_verifies_and_atomically_publishes_model(self):
        manager = DeepModelManager(self.root, self.spec)
        manager.install(
            session_get=lambda url, **kwargs: FakeResponse(self.payload),
            cancel_event=threading.Event(),
        )
        self.assertEqual(DeepModelStatus.READY, manager.status())
        self.assertEqual(self.payload, manager.model_path.read_bytes())
        self.assertFalse(manager.partial_path.exists())

    def test_bad_digest_never_replaces_model(self):
        manager = DeepModelManager(self.root, self.spec)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            manager.install(
                session_get=lambda url, **kwargs: FakeResponse(b"bad"),
                cancel_event=threading.Event(),
            )
        self.assertEqual(DeepModelStatus.NOT_INSTALLED, manager.status())
        self.assertFalse(manager.model_path.exists())
```

- [ ] **Step 2：运行测试确认管理器缺失**

Run: `python -m unittest tests.test_image_similarity_model_manager -v`

Expected: import failure for `tools.image_similarity.model_manager`。

- [ ] **Step 3：实现固定 HTTPS、流式摘要和 `os.replace` 安装**

`DeepModelManager` 公共接口固定为：

```python
class DeepModelManager:
    def __init__(self, storage_dir: Path, spec: DeepModelSpec):
        self.storage_dir = Path(storage_dir)
        self.spec = spec
        self.model_path = self.storage_dir / f"{spec.model_id}-{spec.version}.onnx"
        self.partial_path = self.model_path.with_suffix(".onnx.part")

    def status(self) -> DeepModelStatus:
        if not self.model_path.exists():
            return DeepModelStatus.NOT_INSTALLED
        try:
            stat_result = self.model_path.stat()
            if stat_result.st_size != self.spec.size_bytes:
                return DeepModelStatus.CORRUPT
            if self._sha256(self.model_path) != self.spec.sha256:
                return DeepModelStatus.CORRUPT
        except OSError:
            return DeepModelStatus.CORRUPT
        return DeepModelStatus.READY

    def install(
        self,
        *,
        session_get,
        cancel_event,
        progress=None,
    ) -> Path:
        if not self.spec.download_url.startswith("https://"):
            raise ValueError("深度模型只允许从固定 HTTPS 地址下载")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.partial_path.unlink(missing_ok=True)
        response = session_get(
            self.spec.download_url,
            stream=True,
            timeout=(10, 60),
        )
        received = 0
        digest = hashlib.sha256()
        try:
            response.raise_for_status()
            with self.partial_path.open("xb") as target:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if cancel_event.is_set():
                        raise ModelInstallCancelled("模型安装已取消")
                    if not chunk:
                        continue
                    target.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if progress is not None:
                        progress(received, self.spec.size_bytes)
            if received != self.spec.size_bytes:
                raise ValueError("深度模型文件大小不匹配")
            if digest.hexdigest() != self.spec.sha256:
                raise ValueError("深度模型 SHA-256 校验失败")
            os.replace(self.partial_path, self.model_path)
            return self.model_path
        finally:
            response.close()
            self.partial_path.unlink(missing_ok=True)
```

补齐 `_sha256()`、`ModelInstallCancelled` 和异常清理。删除 `.part` 仅限 `storage_dir` 内固定解析路径；不得删除其他未知文件。模型清单加载器拒绝未知字段、非 64 位十六进制 SHA、非正尺寸和非 HTTPS URL。

- [ ] **Step 4：运行模型管理测试**

Run: `python -m unittest tests.test_image_similarity_model_manager -v`

Expected: all tests `OK`，取消与错误测试确认没有正式模型和 `.part` 残留。

- [ ] **Step 5：提交模型管理器**

```powershell
git add -- tools/image_similarity/model_manager.py tests/test_image_similarity_model_manager.py
git commit -m "feat(image-similarity): 增加深度模型安全安装"
```

## Task 4：版本化全局特征缓存

**Files:**

- Create: `tools/image_similarity/deep_feature_cache.py`
- Create: `tests/test_image_similarity_deep_feature_cache.py`

- [ ] **Step 1：写失败测试覆盖命中、版本隔离和损坏行淘汰**

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from tools.image_similarity.deep_feature_cache import DeepFeatureCache


class DeepFeatureCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.cache = DeepFeatureCache(Path(self.temp.name) / "features.sqlite3")

    def tearDown(self):
        self.cache.close()
        self.temp.cleanup()

    def test_round_trip_uses_content_and_model_key(self):
        vector = np.arange(384, dtype=np.float32)
        vector /= np.linalg.norm(vector)
        self.cache.put("a" * 64, "model:1:prep1", vector)
        loaded = self.cache.get("a" * 64, "model:1:prep1", 384)
        self.assertTrue(np.allclose(vector, loaded))
        self.assertIsNone(self.cache.get("a" * 64, "model:2:prep1", 384))

    def test_wrong_dimension_is_removed_as_cache_miss(self):
        self.cache.put("b" * 64, "model:1:prep1", np.ones(8, dtype=np.float32))
        self.assertIsNone(self.cache.get("b" * 64, "model:1:prep1", 384))
        self.assertIsNone(self.cache.get("b" * 64, "model:1:prep1", 8))
```

- [ ] **Step 2：运行测试确认缓存模块缺失**

Run: `python -m unittest tests.test_image_similarity_deep_feature_cache -v`

Expected: import failure。

- [ ] **Step 3：实现单线程 SQLite 缓存和严格向量校验**

缓存表固定为：

```sql
CREATE TABLE IF NOT EXISTS global_features (
    content_sha256 TEXT NOT NULL,
    model_key TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (content_sha256, model_key)
)
```

`put()` 只接受一维、有限、非零、L2 已归一化的 `float32`；`get()` 校验字节长度、维度、有限值和范数区间 `[0.999, 1.001]`。损坏行在同一事务中删除并返回 `None`。连接只在扫描工作线程首次使用时创建，`close()` 幂等。使用 `PRAGMA journal_mode=WAL` 和 `PRAGMA busy_timeout=3000`，但不跨线程共享同一连接。

- [ ] **Step 4：运行缓存测试并验证数据库关闭后可移动**

Run: `python -m unittest tests.test_image_similarity_deep_feature_cache -v`

Expected: all tests `OK`；Windows 下临时目录清理成功，证明连接已关闭。

- [ ] **Step 5：提交特征缓存**

```powershell
git add -- tools/image_similarity/deep_feature_cache.py tests/test_image_similarity_deep_feature_cache.py
git commit -m "feat(image-similarity): 缓存版本化全局视觉特征"
```

## Task 5：全局近邻候选与裁剪哈希救援候选

**Files:**

- Create: `tools/image_similarity/deep_index.py`
- Create: `tests/test_image_similarity_deep_index.py`

- [ ] **Step 1：写失败测试锁定确定性、目标召回和候选边上限**

```python
import unittest

import numpy as np

from tools.image_similarity.deep_index import CandidateIndexMetrics, build_candidate_pairs


class DeepIndexTests(unittest.TestCase):
    def test_near_global_vectors_are_candidates(self):
        vectors = np.eye(4, 384, dtype=np.float32)
        vectors[1] = vectors[0] * 0.99 + vectors[1] * 0.01
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        pairs = build_candidate_pairs(
            orders=(10, 11, 12, 13),
            vectors=vectors,
            crop_hashes=((), (), (), ()),
            minimum_global_similarity=0.70,
        )
        self.assertIn((10, 11), pairs)

    def test_ten_thousand_vectors_do_not_emit_quadratic_edges(self):
        generator = np.random.default_rng(20260823)
        vectors = generator.normal(size=(10_000, 384)).astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        metrics = CandidateIndexMetrics()
        pairs = build_candidate_pairs(
            orders=tuple(range(10_000)),
            vectors=vectors,
            crop_hashes=tuple(() for _ in range(10_000)),
            minimum_global_similarity=0.95,
            metrics=metrics,
        )
        self.assertLess(metrics.projected_neighbor_checks, 1_000_000)
        self.assertLess(len(pairs), 1_000_000)

    def test_crop_segment_can_rescue_global_miss(self):
        vectors = np.eye(2, 384, dtype=np.float32)
        pairs = build_candidate_pairs(
            orders=(1, 2),
            vectors=vectors,
            crop_hashes=((0xAAAA,), (0xAAAB,)),
            minimum_global_similarity=0.95,
        )
        self.assertIn((1, 2), pairs)
```

- [ ] **Step 2：运行测试确认候选模块缺失**

Run: `python -m unittest tests.test_image_similarity_deep_index -v`

Expected: import failure。

- [ ] **Step 3：实现三投影近邻并集、原向量复核与裁剪 BK-tree**

`build_candidate_pairs()` 固定默认值：三个随机种子 `(20260823, 20260824, 20260825)`、每个投影 64 维、每个图片查询 32 个邻居。每个投影矩阵由对应种子生成高斯矩阵并按列归一化；使用 `scipy.spatial.cKDTree` 查询。候选对统一为 `(min(order), max(order))`，随后用原始 384 维向量精确计算余弦相似度，低于宽松全局阈值的候选不进入深度分支。

裁剪哈希为每个 segment 建立现有 `BKTree`，查询半径固定为 16；同一图片的多段命中去重。单段查询超过 64 个图片时视为密集碰撞并放弃该段，防止纯色或常见背景展开平方级边。裁剪救援对即使全局分数低于初筛，也进入局部复核，但最终仍必须满足所选档位的深度或经典规则。

公开指标：

```python
@dataclass
class CandidateIndexMetrics:
    projected_neighbor_checks: int = 0
    exact_global_checks: int = 0
    crop_hash_checks: int = 0
    emitted_pairs: int = 0
```

函数在每个投影、每个图片和裁剪段查询之间调用 `cancelled()`；取消时抛出复用的 `GroupingCancelled`，不返回半成品候选。

- [ ] **Step 4：运行候选索引测试**

Run: `python -m unittest tests.test_image_similarity_deep_index -v`

Expected: all tests `OK`；一万向量测试的候选检查数低于断言上限，且不分配 `10000 × 10000` 矩阵。

- [ ] **Step 5：提交候选索引**

```powershell
git add -- tools/image_similarity/deep_index.py tests/test_image_similarity_deep_index.py
git commit -m "feat(image-similarity): 增加深度特征近邻候选索引"
```

## Task 6：Patch 空间一致性、经典佐证与三档融合

**Files:**

- Create: `tools/image_similarity/deep_matching.py`
- Create: `tests/test_image_similarity_deep_matching.py`

- [ ] **Step 1：写失败测试覆盖轻微位移、不同布局和三档包含关系**

```python
import unittest

import numpy as np

from tools.image_similarity.deep_matching import (
    classify_deep_evidence,
    match_patch_descriptors,
)
from tools.image_similarity.deep_models import (
    ClassicCorroboration,
    DeepPatchDescriptor,
    DeepSimilarityThresholds,
    SimilarityEvidence,
    SimilarityRelation,
)
from tools.image_similarity.models import SimilarityPreset


class DeepMatchingTests(unittest.TestCase):
    def setUp(self):
        self.thresholds = {
            SimilarityPreset.STRICT: DeepSimilarityThresholds(0.90, 0.70, 0.75, 0.60),
            SimilarityPreset.STANDARD: DeepSimilarityThresholds(0.82, 0.55, 0.55, 0.50),
            SimilarityPreset.LOOSE: DeepSimilarityThresholds(0.72, 0.30, 0.00, 0.40),
        }

    def test_consistent_patch_grid_has_high_coverage_and_geometry(self):
        vectors = np.eye(16, dtype=np.float32)
        first = DeepPatchDescriptor(vectors=vectors, rows=4, columns=4)
        second = DeepPatchDescriptor(vectors=vectors.copy(), rows=4, columns=4)
        result = match_patch_descriptors(first, second, minimum_similarity=0.60)
        self.assertEqual(1.0, result.coverage)
        self.assertEqual(1.0, result.spatial_consistency)

    def test_strict_accepts_high_deep_evidence_with_one_corroboration(self):
        evidence = SimilarityEvidence(
            first_order=1,
            second_order=2,
            global_similarity=0.94,
            patch_coverage=0.78,
            spatial_consistency=0.82,
            mean_patch_similarity=0.88,
            corroborations=(ClassicCorroboration.GRAYSCALE,),
            relation=SimilarityRelation.INTELLIGENT_HIGH,
        )
        relation = classify_deep_evidence(
            evidence,
            SimilarityPreset.STRICT,
            self.thresholds,
        )
        self.assertEqual(SimilarityRelation.INTELLIGENT_HIGH, relation)

    def test_same_subject_without_geometry_fails_strict_but_can_pass_loose(self):
        evidence = SimilarityEvidence(
            first_order=1,
            second_order=2,
            global_similarity=0.93,
            patch_coverage=0.42,
            spatial_consistency=0.20,
            mean_patch_similarity=0.65,
            corroborations=(),
            relation=SimilarityRelation.INTELLIGENT_RELATED,
        )
        self.assertIsNone(
            classify_deep_evidence(evidence, SimilarityPreset.STRICT, self.thresholds)
        )
        self.assertEqual(
            SimilarityRelation.INTELLIGENT_RELATED,
            classify_deep_evidence(evidence, SimilarityPreset.LOOSE, self.thresholds),
        )
```

- [ ] **Step 2：运行测试确认匹配模块缺失**

Run: `python -m unittest tests.test_image_similarity_deep_matching -v`

Expected: import failure。

- [ ] **Step 3：实现双向最近邻和可解释融合**

Patch 匹配使用矩阵乘法 `first.vectors @ second.vectors.T`。仅保留 `argmax` 双向一致且相似度不低于传入阈值的匹配。覆盖率取两侧覆盖比例的较小值；空间一致性按以下固定方法计算：

```python
first_coordinates = normalized_grid(first.rows, first.columns)[first_indexes]
second_coordinates = normalized_grid(second.rows, second.columns)[second_indexes]
displacements = second_coordinates - first_coordinates
median_displacement = np.median(displacements, axis=0)
residuals = np.linalg.norm(displacements - median_displacement, axis=1)
spatial_consistency = float(np.mean(residuals <= 0.12))
```

若没有匹配，四项局部指标返回 `0.0`，不能产生 NaN。`classic_corroborations()` 使用现有函数计算：宽高比满足当前经典档位、灰度/RGBA 达到档位阈值、pHash/dHash 处于两倍经典候选半径、裁剪多段至少一对汉明距离不大于 16。

融合规则必须逐字落实规格：

```python
def _passes(evidence, thresholds, preset):
    metrics = (
        evidence.global_similarity,
        evidence.patch_coverage,
        evidence.spatial_consistency,
        evidence.mean_patch_similarity,
    )
    if any(value is None for value in metrics):
        return False
    global_ok = evidence.global_similarity >= thresholds.global_similarity
    patch_ok = (
        evidence.patch_coverage >= thresholds.patch_coverage
        and evidence.mean_patch_similarity >= thresholds.patch_similarity
    )
    spatial_ok = evidence.spatial_consistency >= thresholds.spatial_consistency
    corroborated = bool(evidence.corroborations)
    if preset is SimilarityPreset.STRICT:
        return global_ok and patch_ok and spatial_ok and corroborated
    if preset is SimilarityPreset.STANDARD:
        return global_ok and patch_ok and (spatial_ok or corroborated)
    return global_ok and patch_ok
```

`classify_deep_evidence()` 从严格到宽松依次测试。所选档位为严格时只允许严格通过；为标准时允许严格或标准通过并返回 `INTELLIGENT_HIGH`；为宽松时，严格或标准通过返回 `INTELLIGENT_HIGH`，仅宽松通过返回 `INTELLIGENT_RELATED`。

- [ ] **Step 4：运行匹配测试与单调性测试**

Run:

```powershell
python -m unittest tests.test_image_similarity_deep_models tests.test_image_similarity_deep_matching -v
```

Expected: all tests `OK`。

- [ ] **Step 5：提交局部匹配与融合**

```powershell
git add -- tools/image_similarity/deep_matching.py tests/test_image_similarity_deep_matching.py
git commit -m "feat(image-similarity): 融合局部构图与深度相似证据"
```

## Task 7：把深度候选接入代表图直接分组

**Files:**

- Modify: `tools/image_similarity/grouping.py:596-961`
- Modify: `tests/test_image_similarity_grouping.py`

- [ ] **Step 1：写失败测试证明传统哈希不能否决深度严格分支**

在现有 `_fingerprint()` 工具上构造 pHash 12、dHash 13 的图片对，并提供高深度证据：

```python
def test_deep_strict_evidence_can_rescue_classic_hash_miss(self):
    first = self._fingerprint("first.png", 0, phash=0, dhash=0)
    second = self._fingerprint(
        "second.png",
        1,
        phash=(1 << 12) - 1,
        dhash=(1 << 13) - 1,
    )
    evidence = SimilarityEvidence(
        first_order=0,
        second_order=1,
        global_similarity=0.95,
        patch_coverage=0.80,
        spatial_consistency=0.84,
        mean_patch_similarity=0.88,
        corroborations=(ClassicCorroboration.GRAYSCALE,),
        relation=SimilarityRelation.INTELLIGENT_HIGH,
    )
    groups = build_similarity_groups(
        (first, second),
        SimilarityPreset.STRICT,
        deep_evidence=(evidence,),
        deep_thresholds=self.deep_thresholds,
    )
    self.assertEqual(1, len(groups))
    self.assertEqual(SimilarityRelation.INTELLIGENT_HIGH, groups[0].relation)

def test_deep_edges_do_not_expand_group_transitively(self):
    groups = build_similarity_groups(
        (self.a, self.b, self.c),
        SimilarityPreset.STRICT,
        deep_evidence=(self.high_ab, self.high_bc),
        deep_thresholds=self.deep_thresholds,
    )
    self.assertFalse(any({0, 1, 2} == {m.record.order for m in g.members} for g in groups))
```

- [ ] **Step 2：运行测试确认新参数不存在**

Run: `python -m unittest tests.test_image_similarity_grouping -v`

Expected: `build_similarity_groups()` 拒绝 `deep_evidence` 参数。

- [ ] **Step 3：合并候选来源但保持经典快速路径**

把深度证据转为按 pair key 的只读字典和按图片 order 的邻接表。每个 seed 的候选为“现有索引候选 ∪ 深度邻接候选”。深度候选跳过 pHash/dHash 预过滤，但必须由 `classify_deep_evidence()` 通过；经典候选仍走现有 `is_visually_similar()`，不能降低现有阈值。

函数签名变为：

```python
def build_similarity_groups(
    fingerprints,
    preset,
    *,
    metrics=None,
    cancelled=None,
    deep_evidence=(),
    deep_thresholds=None,
) -> tuple[SimilarityGroup, ...]:
```

接受顺序：完全 SHA 簇优先；否则经典完整通过标记 `CLASSIC_NEAR_DUPLICATE`；否则查找深度证据并按档位分类。经典通过时构造深度指标均为 `None`、但包含实际经典佐证的 `SimilarityEvidence`；完全重复成员的深度指标为 `None` 且关系为 `EXACT`。组的 `relation` 取组内非完全关系中风险最高者：`INTELLIGENT_RELATED` 高于 `INTELLIGENT_HIGH`，后者高于 `CLASSIC_NEAR_DUPLICATE`；纯同 SHA 组为 `EXACT`。`SimilarityGroup.evidence` 只保存代表图到已接受成员的证据，按成员枚举顺序稳定排序。

深度边只作为候选，不允许把 B 的邻居继续扩张进 A 的组；`assigned` 和 seed 直接复核语义保持现状。

- [ ] **Step 4：运行经典与深度分组回归**

Run:

```powershell
python -m unittest tests.test_image_similarity_grouping tests.test_image_similarity_deep_matching -v
```

Expected: all tests `OK`；现有一万碰撞索引测试仍通过。

- [ ] **Step 5：提交分组集成**

```powershell
git add -- tools/image_similarity/grouping.py tests/test_image_similarity_grouping.py
git commit -m "feat(image-similarity): 接入可解释深度相似分组"
```

## Task 8：扫描器智能阶段、缓存、取消与保守重分组

**Files:**

- Modify: `tools/image_similarity/scanner.py:193-541`
- Modify: `tools/image_similarity_tool.py:738-797`
- Modify: `tests/test_image_similarity_scanner.py`
- Modify: `tests/test_image_similarity_tool.py`
- Modify: `tests/test_image_similarity_recycle_bin.py`

- [ ] **Step 1：写失败测试定义智能扫描协议**

新增 `FakeDeepBackend`，记录全局和 Patch 调用。测试覆盖：

```python
def test_intelligent_scan_hashes_all_images_and_uses_global_cache(self):
    backend = FakeDeepBackend()
    cache = MemoryDeepCache()
    scanner = ImageScanner(deep_backend=backend, deep_cache=cache, deep_thresholds=self.thresholds)
    first = scanner.scan(self.root, scan_mode=ScanMode.INTELLIGENT)
    second = scanner.scan(self.root, scan_mode=ScanMode.INTELLIGENT)
    self.assertFalse(first.cancelled)
    self.assertTrue(all(item.sha256 for item in first.fingerprints))
    self.assertEqual(ScanMode.INTELLIGENT, first.scan_mode)
    self.assertEqual(len(first.fingerprints), backend.global_calls)
    self.assertEqual(len(first.fingerprints), cache.hit_count)

def test_cancel_during_deep_global_returns_no_formal_result(self):
    cancel = threading.Event()
    backend = CancellingDeepBackend(cancel)
    result = ImageScanner(
        deep_backend=backend,
        deep_cache=MemoryDeepCache(),
        deep_thresholds=self.thresholds,
    ).scan(self.root, scan_mode=ScanMode.INTELLIGENT, cancel_event=cancel)
    self.assertTrue(result.cancelled)
    self.assertEqual((), result.groups)
    self.assertEqual((), result.fingerprints)

def test_classic_mode_does_not_construct_or_call_deep_backend(self):
    backend = FailingIfCalledBackend()
    result = ImageScanner(deep_backend=backend).scan(
        self.root,
        scan_mode=ScanMode.CLASSIC_ONLY,
    )
    self.assertEqual(ScanMode.CLASSIC_ONLY, result.scan_mode)
```

在 UI 重分组测试增加：删除原代表后只允许使用 `ScanResult.deep_evidence` 中存在的边重新成组；缺少新代表到成员的深度边时保守拆组，不得根据旧代表关系推断。

- [ ] **Step 2：运行扫描器和重分组测试确认协议缺失**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_image_similarity_scanner tests.test_image_similarity_tool tests.test_image_similarity_recycle_bin -v
```

Expected: `ImageScanner` 不接受深度依赖或 `scan_mode`。

- [ ] **Step 3：实现智能扫描阶段和故障隔离**

`ImageScanner.__init__()` 增加可注入的 `deep_backend`、`deep_cache`、`deep_thresholds` 和 `candidate_builder`。`scan()` 增加默认 `ScanMode.CLASSIC_ONLY` 参数以保持调用兼容。

智能模式按以下顺序执行：

1. 经典指纹；
2. 对全部成功指纹执行 `_hash_fingerprint()`，相同 SHA 只选 `select_suggested_keep()` 的内容代表进入深度阶段；
3. `DEEP_GLOBAL`：先查缓存，未命中才调用后端并立即写入缓存；
4. `CANDIDATES`：把代表 orders、全局矩阵和裁剪段传给 `build_candidate_pairs()`；
5. `DEEP_LOCAL`：使用容量 64 的进程内 Patch LRU，每个候选图最多推理一次；
6. 构造 `SimilarityEvidence`，只保留至少通过宽松深度规则或可作为经典救援候选的标量边；
7. `GROUPING`：把证据和阈值传给 `build_similarity_groups()`；
8. `ScanResult` 保存 `scan_mode` 与排序后的 `deep_evidence`。

扫描协调主干固定为以下结构，所有 helper 都在前述任务中定义，不在 UI 中实现算法：

```python
if scan_mode is ScanMode.INTELLIGENT:
    ordered_fingerprints = self._hash_all_fingerprints(
        fingerprints_by_order,
        failures,
        reporter,
        cancellation,
    )
    content_representatives = self._content_representatives(ordered_fingerprints)
    global_descriptors = self._load_global_descriptors(
        content_representatives,
        reporter,
        cancellation,
    )
    candidate_pairs = self.candidate_builder(
        orders=tuple(item.record.order for item in content_representatives),
        vectors=np.stack(
            [global_descriptors[item.record.order].vector for item in content_representatives]
        ),
        crop_hashes=tuple(item.crop_hash_segments for item in content_representatives),
        minimum_global_similarity=self.deep_thresholds[
            SimilarityPreset.LOOSE
        ].global_similarity,
        cancelled=cancellation.is_set,
    )
    deep_evidence = self._evaluate_deep_candidates(
        candidate_pairs,
        content_representatives,
        global_descriptors,
        reporter,
        cancellation,
    )
else:
    ordered_fingerprints = self._hash_classic_duplicate_candidates(
        fingerprints_by_order,
        failures,
        reporter,
        cancellation,
    )
    deep_evidence = ()

groups = build_similarity_groups(
    ordered_fingerprints,
    preset,
    cancelled=cancellation.is_set,
    deep_evidence=deep_evidence,
    deep_thresholds=self.deep_thresholds,
)
```

每个批次和候选对检查 `cancel_event`。单图深度失败加入 `ScanFailure(code=DEEP_FEATURE_FAILED)` 并排除该图的深度边，但不删除已成功的经典指纹；模型级加载或输出契约错误抛出任务级异常，由 `ImageScanWorker.failed` 交给 UI。`deep_cache.close()` 放在 `finally` 中。

`_rebuild_result_without_paths()` 调用分组器时重新传入过滤后的 `result.deep_evidence` 和同版本阈值。没有深度边的新代表关系按不相似处理，允许组保守拆分，禁止链式推断。

- [ ] **Step 4：运行扫描、取消、回收和旧模式回归**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_image_similarity_scanner tests.test_image_similarity_grouping tests.test_image_similarity_tool tests.test_image_similarity_recycle_bin -v
```

Expected: all tests `OK`；无真实 ONNX、网络和回收站调用。

- [ ] **Step 5：提交扫描器集成**

```powershell
git add -- tools/image_similarity/scanner.py tools/image_similarity_tool.py tests/test_image_similarity_scanner.py tests/test_image_similarity_tool.py tests/test_image_similarity_recycle_bin.py
git commit -m "feat(image-similarity): 编排智能视觉扫描阶段"
```

## Task 9：模型安装 UI、档位说明、证据标签和宽松档保护

**Files:**

- Modify: `tools/image_similarity_tool.py:1-84,405-664,798-1245,1477-1510`
- Modify: `tests/test_image_similarity_tool.py`

- [ ] **Step 1：写离屏失败测试定义 UI 安全交互**

测试注入假的模型管理器和安装 worker 工厂：

```python
def test_strict_mode_description_matches_intelligent_definition(self):
    self.assertIn("同主体、同动作", self.tool.preset_description_label.text())

def test_missing_model_requires_explicit_download_or_classic_choice(self):
    self.tool.model_manager = FakeModelManager(DeepModelStatus.NOT_INSTALLED)
    with patch.object(self.tool, "_ask_missing_model", return_value=ScanStartChoice.CANCEL):
        self.tool.start_scan()
    self.assertIsNone(self.tool.scan_thread)

def test_result_displays_intelligent_relation_and_scan_mode(self):
    result = self._scan_result(
        scan_mode=ScanMode.INTELLIGENT,
        relation=SimilarityRelation.INTELLIGENT_HIGH,
    )
    self.tool.show_scan_result(result)
    self.assertIn("智能混合扫描", self.tool.result_summary_label.text())
    relation_index = self.tool.image_model.index(1, 7)
    self.assertEqual("智能高度相似", self.tool.image_model.data(relation_index))

def test_related_group_requires_confirmation_before_bulk_select(self):
    self.tool.image_model.set_group(self._related_group())
    with patch.object(QMessageBox, "warning") as warning:
        self.tool.select_group_others()
    warning.assert_called_once()
    self.assertEqual(set(), self.tool.selected_paths)
```

- [ ] **Step 2：运行 UI 测试确认控件和选择协议缺失**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_image_similarity_tool -v
```

Expected: 新控件或关系标签断言失败。

- [ ] **Step 3：实现模型状态区和非阻塞安装 worker**

在扫描面板增加：

- `model_status_label`：显示未安装、下载中、校验中、就绪、损坏或不兼容；
- `install_model_button`：明确的“下载并安装智能模型”；
- `scan_mode_label`：显示智能混合扫描或仅经典算法；
- `preset_description_label`：随三档单选按钮更新定义。

应用数据目录使用：

```python
base = Path(
    QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    )
)
model_dir = base / "image_similarity" / "models"
cache_path = base / "image_similarity" / "deep_features.sqlite3"
```

模型下载运行在独立 `QThread` 的 `ModelInstallWorker` 中；worker 调用 `requests.get` 注入模型管理器，发送 `(received, total)` 进度、完成、失败和 finished 信号。下载期间禁用开始扫描和重复安装；取消关闭窗口时设置安装取消事件并等待线程正常结束，不能 `terminate()`。

点击开始扫描且模型未就绪时，只允许三个明确结果：下载并安装、仅经典算法扫描、取消。选择下载时安装成功后不自动扫描，避免用户未确认目录状态就启动长任务；用户再次点击开始扫描。

- [ ] **Step 4：实现关系证据、结果模式和宽松批量确认**

扩展 `PHASE_LABELS`：全局特征、候选生成、局部智能复核。扩展关系标签：

```python
RELATION_LABELS = {
    SimilarityRelation.EXACT: "完全相同",
    SimilarityRelation.CLASSIC_NEAR_DUPLICATE: "经典近重复",
    SimilarityRelation.INTELLIGENT_HIGH: "智能高度相似",
    SimilarityRelation.INTELLIGENT_RELATED: "智能内容相关",
}
```

`ImageTableModel._relation_label()` 优先读取当前组中对应 member order 的证据；代表图显示“组内代表”。证据 tooltip 使用“全局/局部/构图/经典佐证”的高、中等级，不显示原始阈值。`GroupListModel` 显示组主关系。

`select_group_others()` 遇到 `INTELLIGENT_RELATED` 时先显示风险确认；取消保持选择集合不变，确认才调用现有 `select_all_except_suggested()`。所有档位和关系继续默认不选中。

- [ ] **Step 5：运行 UI 与线程生命周期测试**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_image_similarity_tool tests.test_main_window tests.test_theme_utils -v
```

Expected: all tests `OK`；测试不访问网络、不启动真实 ONNX 会话。

- [ ] **Step 6：提交 UI 集成**

```powershell
git add -- tools/image_similarity_tool.py tests/test_image_similarity_tool.py
git commit -m "feat(image-similarity): 展示智能模型与相似证据"
```

## Task 10：模型清单生成、阈值校准与本地目标验收

**Files:**

- Create: `scripts/calibrate_deep_similarity.py`
- Create: `tools/image_similarity/assets/dinov2_vits14.json`
- Create: `tools/image_similarity/assets/dinov2_vits14_thresholds.json`
- Create: `tools/image_similarity/assets/DINOV2_LICENSE.txt`
- Create: `tests/test_image_similarity_calibration.py`
- Create: `tests/test_image_similarity_deep_golden.py`
- Modify: `.gitignore`
- Modify: `tools/image_similarity/deep_models.py`
- Modify: `tools/image_similarity_tool.py`

- [ ] **Step 1：写失败测试锁定配置生成的确定性和拒绝无效阈值**

```python
def test_calibration_is_deterministic_and_monotonic(self):
    first = calibrate_thresholds(self.labeled_metrics)
    second = calibrate_thresholds(tuple(reversed(self.labeled_metrics)))
    self.assertEqual(first, second)
    validate_threshold_order(first)

def test_runtime_manifest_rejects_wrong_model_digest(self):
    manifest = load_model_manifest(self.manifest_path)
    self.assertEqual(64, len(manifest.sha256))
    self.assertTrue(all(character in "0123456789abcdef" for character in manifest.sha256))
```

校准 fixture 使用确定性标量指标，不需要真实图片或模型。严格档样本目标是高精确率：所有标注严格负样本必须被拒绝；在满足该条件的边界中选择严格正样本召回率最高的一组。标准档在不破坏严格包含关系时最大化 F1；宽松档在不破坏单调性时最大化召回率。分数并列时选择更严格阈值，保证结果稳定。

- [ ] **Step 2：运行校准测试确认脚本和加载器缺失**

Run: `python -m unittest tests.test_image_similarity_calibration -v`

Expected: 导入失败。

- [ ] **Step 3：实现标注 JSONL、网格搜索和版本化输出**

输入 JSONL 每行固定字段：`pair_id`、`label`、`global_similarity`、`patch_coverage`、`spatial_consistency`、`mean_patch_similarity`、`corroboration_count`。`label` 只允许 `strict_positive`、`standard_positive`、`loose_positive`、`negative`。脚本按固定候选分数集合搜索，输出包含 `model_id`、`model_version`、`preprocessing_version`、`calibration_version`、三个档位阈值和数据集 SHA-256 的 JSON；键排序、UTF-8、末尾换行固定。

运行时加载器校验模型 ID、版本、预处理版本、所有数值范围和三档单调性。清单或阈值不兼容时模型状态为 `INCOMPATIBLE`，禁止智能扫描。公共加载接口固定为：

```python
ASSET_DIRECTORY = Path(__file__).with_name("assets")


def load_default_model_spec() -> DeepModelSpec:
    payload = json.loads(
        (ASSET_DIRECTORY / "dinov2_vits14.json").read_text(encoding="utf-8")
    )
    return model_spec_from_dict(payload)


def load_default_deep_thresholds():
    payload = json.loads(
        (ASSET_DIRECTORY / "dinov2_vits14_thresholds.json").read_text(
            encoding="utf-8"
        )
    )
    thresholds = thresholds_from_dict(payload)
    validate_threshold_order(thresholds)
    return freeze_thresholds(thresholds)
```

`DINOV2_LICENSE.txt` 保存固定权重对应官方仓库的 Apache 2.0 许可文本，并在文件首行记录来源 `https://github.com/facebookresearch/dinov2/blob/main/LICENSE`；不从模型下载响应中动态生成许可。

- [ ] **Step 4：在隔离导出环境生成并验证模型，不提交二进制**

经用户授权联网下载权重后执行：

```powershell
python -m pip install -r requirements-model-export.txt
python scripts/export_dinov2_onnx.py --output .local/models/dinov2_vits14.onnx
python scripts/verify_dinov2_onnx.py --model .local/models/dinov2_vits14.onnx
```

Expected: 验证脚本报告全局与 Patch 形状一致、误差在 Task 2 限制内并退出 0。`.local/models/dinov2_vits14.onnx` 必须被 `.gitignore` 排除。

在 `.gitignore` 的运行时输出段加入：

```gitignore
.local/
```

用脚本计算实际文件大小和 SHA-256，并生成下载 URL 固定为：

```text
https://github.com/nihuii/FireflyTools/releases/download/image-similarity-dinov2-v1/dinov2_vits14.onnx
```

生成 `tools/image_similarity/assets/dinov2_vits14.json`。远端 Release 资产上传不在此提交权限内；上传前本地黄金测试直接通过环境变量使用已验证模型，正式发布前必须另行获得上传授权并验证下载 URL。

- [ ] **Step 5：采集本地评测指标并生成阈值文件**

创建不提交的 `.local/deep-similarity-eval.jsonl`，至少包含：

- `pic_test` 目标对，标记 `strict_positive`；
- 两张目标图片分别与仓库壁纸的负样本；
- 同主体不同动作、同背景不同主体和相似配色不同内容的授权负样本；
- 程序生成的缩放、压缩、色偏和轻裁剪正样本。

执行：

```powershell
python scripts/calibrate_deep_similarity.py --input .local/deep-similarity-eval.jsonl --output tools/image_similarity/assets/dinov2_vits14_thresholds.json --model-manifest tools/image_similarity/assets/dinov2_vits14.json
python -m unittest tests.test_image_similarity_calibration tests.test_image_similarity_deep_models -v
```

Expected: 输出三档阈值通过单调性测试；脚本打印各档混淆矩阵，严格档负样本误报为 0。若严格档无法同时接受目标对并拒绝难负样本，停止实施并回到模型/Patch 设计，不通过手工降低阈值掩盖问题。

- [ ] **Step 6：运行真实模型本地黄金验收**

在 `tests/test_image_similarity_deep_golden.py` 写入显式跳过且不复制原图的本地验收：

```python
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.image_similarity.deep_feature_cache import DeepFeatureCache
from tools.image_similarity.deep_features import OnnxDeepFeatureBackend
from tools.image_similarity.deep_models import (
    ScanMode,
    load_default_deep_thresholds,
    load_default_model_spec,
)
from tools.image_similarity.models import SimilarityPreset
from tools.image_similarity.scanner import ImageScanner


class DeepGoldenTests(unittest.TestCase):
    def test_pic_test_pair_is_strictly_similar(self):
        repository = Path(__file__).resolve().parents[1]
        model_value = os.environ.get("FIREFLYTOOLS_DEEP_MODEL", "")
        model_path = Path(model_value) if model_value else None
        first = repository / "pic_test" / "01567-1273093529.png"
        second = repository / "pic_test" / "01569-1273093531.png"
        if model_path is None or not model_path.is_file():
            self.skipTest("未配置本地 DINOv2 ONNX 模型")
        if not first.is_file() or not second.is_file():
            self.skipTest("本地 pic_test 验收图片不存在")
        spec = load_default_model_spec()
        thresholds = load_default_deep_thresholds()
        backend = OnnxDeepFeatureBackend(model_path, spec)
        with TemporaryDirectory() as temp_directory:
            cache = DeepFeatureCache(Path(temp_directory) / "features.sqlite3")
            scanner = ImageScanner(
                deep_backend=backend,
                deep_cache=cache,
                deep_thresholds=thresholds,
            )
            result = scanner.scan(
                first.parent,
                recursive=False,
                preset=SimilarityPreset.STRICT,
                scan_mode=ScanMode.INTELLIGENT,
            )
        self.assertFalse(result.cancelled)
        self.assertEqual(1, len(result.groups))
        self.assertEqual(
            {first, second},
            {item.record.path for item in result.groups[0].members},
        )
```

随后执行：

```powershell
$env:FIREFLYTOOLS_DEEP_MODEL='D:\Study\Projects\PythonProject\FireflyTools\.local\models\dinov2_vits14.onnx'
python -m unittest tests.test_image_similarity_deep_golden -v
```

Expected: 两张 `pic_test` 图片在严格档组成一个组。难负样本由 Step 5 的本地校准语料验证；黄金测试在环境变量或本地图片缺失时使用 `unittest.SkipTest`，不得下载或复制原图。

- [ ] **Step 7：提交脚本和文本清单，明确排除模型及本地语料**

```powershell
git add -- .gitignore scripts/calibrate_deep_similarity.py tools/image_similarity/assets/dinov2_vits14.json tools/image_similarity/assets/dinov2_vits14_thresholds.json tools/image_similarity/assets/DINOV2_LICENSE.txt tools/image_similarity/deep_models.py tools/image_similarity_tool.py tests/test_image_similarity_calibration.py tests/test_image_similarity_deep_golden.py
git diff --cached --name-only
git commit -m "feat(image-similarity): 固化深度模型清单与三档阈值"
```

Expected staged list 不包含 `.onnx`、`.local`、`pic_test` 或评测 JSONL。

## Task 11：完整回归、性能审计、文档和交付检查

**Files:**

- Modify: `docs/项目介绍.md`
- Modify: `tests/test_code_documentation.py`
- Verify: `tools/image_similarity/**/*.py`
- Verify: `tools/image_similarity_tool.py`
- Verify: `tests/test_image_similarity*.py`

- [ ] **Step 1：更新用户文档和代码文档约束**

在 `docs/项目介绍.md` 的图片相似度章节说明：

- 三档采用本设计第 6 节的面向用户定义；
- 模型首次使用需要明确下载，图片只在本地处理；
- 模型不可用时可明确选择经典扫描；
- “智能内容相关”不等于重复图片；
- 所有删除项默认未选中且只移入系统回收站；
- 模型版本变化会使旧全局特征缓存失效。

`tests/test_code_documentation.py` 增加新运行时模块必须有模块文档字符串、公共类和公共函数文档字符串的检查。

- [ ] **Step 2：运行图片相似度定向测试**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_image_similarity_fingerprints tests.test_image_similarity_deep_models tests.test_image_similarity_deep_features tests.test_image_similarity_model_manager tests.test_image_similarity_deep_feature_cache tests.test_image_similarity_deep_index tests.test_image_similarity_deep_matching tests.test_image_similarity_calibration tests.test_image_similarity_grouping tests.test_image_similarity_scanner tests.test_image_similarity_recycle_bin tests.test_image_similarity_tool tests.test_main_window tests.test_theme_utils tests.test_code_documentation -v
```

Expected: all non-golden tests `OK`；无网络、真实模型或真实回收站调用。

- [ ] **Step 3：运行完整回归**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

Expected: 当前 235 项基线及新增测试全部 `OK`；真实模型黄金测试在未配置环境变量时只报告预期 `skipped`。四个现有无权限临时目录可能继续产生枚举警告，不得据此删除目录或修改业务逻辑。

- [ ] **Step 4：运行性能与内存边界审计**

Run:

```powershell
python -m unittest tests.test_image_similarity_deep_index.DeepIndexTests.test_ten_thousand_vectors_do_not_emit_quadratic_edges tests.test_image_similarity_grouping.ImageSimilarityGroupingTests.test_ten_thousand_identical_gray_fingerprints_do_not_store_all_edges -v
```

Expected: 两项 `OK`；候选索引不建立 `10000 × 10000` 矩阵，Patch LRU 上限保持 64，缩略图缓存上限保持 128。

- [ ] **Step 5：执行静态、差异和来源审计**

Run:

```powershell
python -m compileall -q tools scripts
git diff --check
git status --short --branch -uall
git diff --stat
```

Expected: 编译和 `git diff --check` 无输出；差异只包含本功能代码、测试、规格、计划和文档。不得出现模型二进制、下载临时文件、缓存数据库、`pic_test` 原图、浏览器 profile 或来源不明文件。

- [ ] **Step 6：人工安全复核**

逐项确认：

- 严格档的深度分支需要全局、局部、空间和至少一种经典佐证；
- pHash/dHash 不再能单项否决深度严格分支；
- 标准和宽松阈值单调，集合包含测试通过；
- 模型未安装、损坏、输出异常和取消不会形成正式智能结果；
- 图片不上传，下载只针对固定模型 URL；
- 宽松智能组批量选择有额外确认；
- 所有删除框默认不选中；
- `RecycleBinService` 的路径、快照和回收站复核未被绕过；
- QPixmap 仍只在主线程创建；
- 模型与本地验收图片未被暂存。

- [ ] **Step 7：提交文档与最终回归记录**

```powershell
git add -- docs/项目介绍.md tests/test_code_documentation.py
git commit -m "docs(image-similarity): 说明智能相似度检测与安全边界"
```

提交后执行：

```powershell
git log --oneline -12
git status --short --branch -uall
```

Expected: 任务提交按计划分离；工作区只保留用户原有未跟踪 `pic_test` 图片和已知来源文件。本计划不授权 push 或 Release 上传。

## 完成定义

- DINOv2 ONNX 参考输出一致性门槛通过。
- `pic_test` 目标对通过本地严格档黄金验收。
- 同主体不同动作等难负样本不进入严格档。
- 三档包含关系有确定性测试。
- 一万图片特征候选测试不退化为全量平方矩阵或 Python 两两循环。
- 模型生命周期、缓存损坏、取消和异常均有自动化覆盖。
- 仅经典模式与全部现有经典算法回归继续通过。
- UI 明确显示扫描模式、关系证据和宽松档风险。
- 模型、本地评测集和 `pic_test` 原图未进入 Git。
- 完整测试、编译、差异检查均有最新通过证据。
