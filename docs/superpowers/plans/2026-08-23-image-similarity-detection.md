# 图片相似度检测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 FireflyTools 增加第 5 个“图片相似度检测”标签页，以混合指纹稳定分组完全重复和视觉相似图片，并只允许用户二次确认后将明确选择的未变化文件移入系统回收站。

**Architecture:** 纯 Python 核心分为数据模型、混合指纹、BK-tree 分组、目录扫描和回收站安全服务；PyQt6 页面只负责后台任务、model/view 展示、懒加载缩略图和用户确认。扫描结果与 Qt 控件解耦，所有文件操作经可注入的回收站后端执行，测试不会真实删除文件。

**Tech Stack:** Python 3.10+、PyQt6、Pillow、ImageHash 4.x、Send2Trash 2.x、`unittest`

---

## 文件结构

**新增运行文件：**

- `tools/image_similarity/__init__.py`：导出稳定公共 API。
- `tools/image_similarity/models.py`：枚举、阈值和不可变结果模型。
- `tools/image_similarity/fingerprints.py`：EXIF 修正、SHA-256、pHash、dHash、灰度复核。
- `tools/image_similarity/grouping.py`：BK-tree、精确副本簇、安全分组和保留建议。
- `tools/image_similarity/scanner.py`：安全枚举、有界并发、取消和进度。
- `tools/image_similarity/recycle_bin.py`：删除前复核与可注入回收站后端。
- `tools/image_similarity_tool.py`：透明 PyQt6 标签页、后台 worker、Qt model/view、缩略图 LRU 与二次确认。

**修改运行文件：**

- `tools/main.py`：注册第 5 个标签页，并刷新无壁纸回退主题。
- `tools/theme_utils.py`：为新增 model/view、进度和确认控件生成动态主题。
- `requirements.txt`：加入 `ImageHash>=4.3,<5` 与 `Send2Trash>=2.1,<3`。

**新增测试文件：**

- `tests/test_image_similarity_fingerprints.py`
- `tests/test_image_similarity_grouping.py`
- `tests/test_image_similarity_scanner.py`
- `tests/test_image_similarity_recycle_bin.py`
- `tests/test_image_similarity_tool.py`

**修改测试文件：**

- `tests/test_main_window.py`
- `tests/test_theme_utils.py`

## Task 1：数据模型、依赖和混合指纹

**Files:**

- Create: `tools/image_similarity/__init__.py`
- Create: `tools/image_similarity/models.py`
- Create: `tools/image_similarity/fingerprints.py`
- Create: `tests/test_image_similarity_fingerprints.py`
- Modify: `requirements.txt`

- [ ] **Step 1: 写失败测试定义公共模型与指纹行为**

测试必须构造临时 PNG/JPEG，不依赖仓库图片：

```python
class FingerprintTests(unittest.TestCase):
    def test_presets_have_monotonic_thresholds(self):
        strict = thresholds_for(SimilarityPreset.STRICT)
        standard = thresholds_for(SimilarityPreset.STANDARD)
        loose = thresholds_for(SimilarityPreset.LOOSE)
        self.assertLessEqual(strict.phash_distance, standard.phash_distance)
        self.assertLessEqual(standard.phash_distance, loose.phash_distance)
        self.assertGreaterEqual(strict.grayscale_similarity, standard.grayscale_similarity)

    def test_same_bytes_have_same_sha256(self):
        self.assertEqual(sha256_file(first), sha256_file(second))

    def test_visual_fingerprint_uses_exif_corrected_pixels(self):
        first = fingerprint_image(oriented_jpeg, order=0)
        second = fingerprint_image(rotated_pixels_png, order=1)
        self.assertEqual(first.record.dimensions, second.record.dimensions)
        self.assertLessEqual(hamming_distance(first.phash, second.phash), 4)
```

- [ ] **Step 2: 运行测试并确认因模块缺失而失败**

Run: `python -m unittest tests.test_image_similarity_fingerprints -v`

Expected: `ModuleNotFoundError: No module named 'tools.image_similarity'`

- [ ] **Step 3: 添加依赖和最小公共类型**

`requirements.txt` 追加：

```text
ImageHash>=4.3,<5
Send2Trash>=2.1,<3
```

`models.py` 定义：

```python
class SimilarityPreset(Enum):
    STRICT = "strict"
    STANDARD = "standard"
    LOOSE = "loose"

@dataclass(frozen=True)
class SimilarityThresholds:
    phash_distance: int
    dhash_distance: int
    aspect_ratio_difference: float
    grayscale_similarity: float

PRESET_THRESHOLDS = {
    SimilarityPreset.STRICT: SimilarityThresholds(4, 4, 0.01, 0.94),
    SimilarityPreset.STANDARD: SimilarityThresholds(8, 8, 0.02, 0.90),
    SimilarityPreset.LOOSE: SimilarityThresholds(12, 10, 0.03, 0.86),
}
```

同时定义 `ScanPhase`、`ScanErrorCode`、`GroupType`、`RecycleStatus`、`ImageRecord`、`ImageFingerprint`、`SimilarityGroup`、`ScanFailure`、`ScanProgress`、`ScanResult` 和 `RecycleItemResult`；路径模型使用 `pathlib.Path`，容器字段使用 tuple，保证结果可稳定比较。

- [ ] **Step 4: 实现指纹最小行为**

`fingerprints.py` 提供：

```python
def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str: ...
def fingerprint_image(path: Path, order: int) -> ImageFingerprint: ...
def hamming_distance(first: int, second: int) -> int: ...
def grayscale_similarity(first: bytes, second: bytes) -> float: ...
def aspect_ratio_difference(first: ImageRecord, second: ImageRecord) -> float: ...
```

`fingerprint_image()` 必须在 `warnings.catch_warnings()` 中把 `Image.DecompressionBombWarning` 转为异常，调用 `ImageOps.exif_transpose()`、完整 `load()`、64 位 `imagehash.phash()` 和 `imagehash.dhash()`，并生成固定 16×16 灰度字节及 8×8 预乘 RGBA 描述符。感知哈希与灰度在固定白底合成图上计算；预乘 RGBA 保留透明度和可见色度，并以较低维度支持大批量候选索引。读取前后比较大小、`mtime_ns` 和文件身份，变化时返回结构化失败，而不是产生不一致快照。

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m unittest tests.test_image_similarity_fingerprints -v`

Expected: all tests `OK`。

## Task 2：BK-tree、精确副本与安全分组

**Files:**

- Create: `tools/image_similarity/grouping.py`
- Create: `tests/test_image_similarity_grouping.py`

- [ ] **Step 1: 写失败测试覆盖候选索引、链式隔离和稳定排序**

```python
class GroupingTests(unittest.TestCase):
    def test_exact_duplicates_form_exact_group(self): ...

    def test_similarity_does_not_expand_transitively(self):
        # A-B 和 B-C 通过，A-C 不通过；A 的组不能包含 C。
        groups = build_similarity_groups((a, b, c), SimilarityPreset.STRICT)
        self.assertNotEqual(group_for(a), group_for(c))

    def test_representative_prefers_pixels_then_size_then_path(self): ...

    def test_bk_tree_reduces_comparisons_for_ten_thousand_hashes(self):
        tree = BKTree()
        for index, value in enumerate(dispersed_hashes(10_000)):
            tree.add(value, index)
        tree.search(dispersed_hashes(10_000)[0], 4)
        self.assertLess(tree.last_search_comparisons, 10_000)
```

- [ ] **Step 2: 运行测试并确认缺少分组模块**

Run: `python -m unittest tests.test_image_similarity_grouping -v`

Expected: import failure for `tools.image_similarity.grouping`。

- [ ] **Step 3: 实现 BK-tree 和直接代表匹配**

`grouping.py` 提供：

```python
class BKTree:
    def add(self, value: int, item_index: int) -> None: ...
    def search(self, value: int, radius: int) -> tuple[int, ...]: ...

def is_visually_similar(first, second, thresholds) -> bool: ...
def select_suggested_keep(members) -> ImageFingerprint: ...
def build_similarity_groups(fingerprints, preset) -> tuple[SimilarityGroup, ...]: ...
```

同 SHA-256 图片先收缩为精确簇，每簇只将代表图加入 BK-tree。按“像素数降序、文件大小降序、规范路径不区分大小写升序”选种子，只接受与种子直接满足 pHash、dHash、宽高比、灰度和预乘 RGBA 阈值的候选；不得用候选继续扩展当前组。双哈希密集时按阈值使用 32 或 64 维灰度分块和 L1 下界索引，再对候选执行原始 256 维精确复核；若灰度邻域仍然密集，则惰性建立一组通道和、两组互补通道差的 32 或 64 维下界索引。每组投影都把 RGBA 通道拆成不重叠通道对，因此均为原始 L1 的必要下界，不会漏掉阈值内候选。首次查询三组投影并按候选数量确定后续顺序；每个种子依次细化到交集不超过 512 项或三组投影全部用完，再执行原始 RGBA 和灰度精确收口。这样既能过滤等亮度异色，也能观察白色随机透明度的共同通道变化。性能回归同时记录投影树实际交付数量，不能只依赖最终复核计数。最后展开精确簇，全部同哈希标记为 `EXACT`，否则标记为 `VISUAL`。

- [ ] **Step 4: 运行分组测试确认通过**

Run: `python -m unittest tests.test_image_similarity_grouping -v`

Expected: all tests `OK`，一万条合成指纹测试不做全量两两比较。

## Task 3：安全目录扫描、并发、进度和取消

**Files:**

- Create: `tools/image_similarity/scanner.py`
- Create: `tests/test_image_similarity_scanner.py`

- [ ] **Step 1: 写失败测试定义扫描边界**

```python
class ScannerTests(unittest.TestCase):
    def test_recursive_switch_controls_nested_images(self): ...
    def test_symlink_is_not_scanned(self): ...
    def test_corrupt_image_does_not_abort_other_images(self): ...
    def test_cancelled_scan_returns_no_formal_groups(self): ...
    def test_results_follow_enumeration_order_not_completion_order(self): ...
```

- [ ] **Step 2: 运行测试确认模块缺失**

Run: `python -m unittest tests.test_image_similarity_scanner -v`

Expected: import failure for `tools.image_similarity.scanner`。

- [ ] **Step 3: 实现枚举和扫描协调器**

`scanner.py` 提供：

```python
SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"})

class ImageScanner:
    def scan(self, root, recursive=True, preset=SimilarityPreset.STRICT,
             progress=None, cancel_event=None) -> ScanResult: ...

class ImageScanWorker(QObject):
    progress_changed = pyqtSignal(object)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    finished = pyqtSignal()
```

使用 `os.scandir()` 捕获逐目录权限错误，不跟随链接和 Windows reparse point。用最大 4 个线程计算视觉指纹，按枚举序号恢复稳定顺序；仅对同大小桶计算 SHA-256，并用独立完整哈希阶段报告进度。进度阶段切换强制发送，中间更新限制为每秒最多 10 次。取消后返回 `cancelled=True` 且 `groups=()`，不暴露半成品为正式结果。

- [ ] **Step 4: 运行扫描测试确认通过**

Run: `python -m unittest tests.test_image_similarity_scanner -v`

Expected: all tests `OK`。

## Task 4：回收站安全复核

**Files:**

- Create: `tools/image_similarity/recycle_bin.py`
- Create: `tests/test_image_similarity_recycle_bin.py`

- [ ] **Step 1: 写失败测试，后端仅记录调用**

```python
class RecycleBinTests(unittest.TestCase):
    def test_safe_unchanged_file_calls_backend_once(self): ...
    def test_changed_file_is_skipped(self): ...
    def test_outside_unscanned_directory_and_symlink_are_rejected(self): ...
    def test_backend_failure_does_not_stop_later_items(self): ...
```

- [ ] **Step 2: 运行测试确认模块缺失**

Run: `python -m unittest tests.test_image_similarity_recycle_bin -v`

Expected: import failure for `tools.image_similarity.recycle_bin`。

- [ ] **Step 3: 实现只回收、不降级的服务**

```python
class RecycleBinService:
    def __init__(self, root: Path, scanned_records, backend=send2trash): ...
    def move_items(self, paths: Iterable[Path]) -> tuple[RecycleItemResult, ...]: ...
```

每项依次校验存在、普通文件、非链接/非 reparse、规范路径位于根目录、属于正式结果、大小、`mtime_ns` 与稳定文件身份未变化；在尽可能紧邻后端调用的位置再次复核路径组件与文件快照。安全失败返回 `SKIPPED_UNSAFE_PATH`，快照变化返回 `SKIPPED_CHANGED`，后端异常返回 `TRASH_OPERATION_FAILED`；任何分支都不得调用 `unlink()`、`remove()` 或永久删除后备。

- [ ] **Step 4: 运行回收站测试确认通过**

Run: `python -m unittest tests.test_image_similarity_recycle_bin -v`

Expected: all tests `OK`，假后端记录的调用符合逐项隔离规则。

## Task 5：PyQt6 页面、缩略图缓存和二次确认

**Files:**

- Create: `tools/image_similarity_tool.py`
- Create: `tests/test_image_similarity_tool.py`

- [ ] **Step 1: 写离屏失败测试定义 UI 默认值与安全交互**

```python
class ImageSimilarityToolTests(unittest.TestCase):
    def test_defaults_are_recursive_strict_and_unselected(self): ...
    def test_root_scroll_area_and_content_are_transparent(self): ...
    def test_trash_button_is_disabled_without_selection(self): ...
    def test_confirmation_lists_count_size_paths_and_recycle_bin_warning(self): ...
    def test_thumbnail_cache_never_exceeds_capacity(self): ...
```

- [ ] **Step 2: 运行测试确认页面模块缺失**

Run: `$env:QT_QPA_PLATFORM='offscreen'; python -m unittest tests.test_image_similarity_tool -v`

Expected: import failure for `tools.image_similarity_tool`。

- [ ] **Step 3: 实现透明页面和 Qt model/view**

`image_similarity_tool.py` 定义：

```python
class ThumbnailCache:
    def __init__(self, capacity: int = 128): ...
    def get(self, path: Path, size: QSize) -> QPixmap: ...

class GroupListModel(QAbstractListModel): ...
class ImageTableModel(QAbstractTableModel): ...
class TrashConfirmationDialog(QDialog): ...
class ImageSimilarityTool(QWidget): ...
```

根控件、`QScrollArea`、viewport 和内容控件均显式透明；扫描区和结果区使用 `QFrame#container` 与 `apply_shadow()`。左侧按全部/完全重复/视觉相似筛选组，右侧表格用 `CheckStateRole` 管理用户选择，初始选择集合为空。“保留建议项并选择本组其他图片”只能由用户点击触发。缩略图在 `DecorationRole` 请求时加载，LRU 上限固定为 128，溢出请求进入有界延迟队列并按扫描代次隔离；模型使用路径到行号索引定向刷新，`QPixmap` 只在主线程创建。

- [ ] **Step 4: 实现 worker 生命周期和二次确认**

扫描任务放入 `QThread`，完成、失败、取消都关闭线程并恢复按钮状态。确认窗口展示数量、格式化总大小、只读完整路径列表，以及“移入系统回收站，不会永久删除”“清空回收站后才会真正释放空间”两条说明；取消按钮为默认焦点，确认按钮不得为 default/auto-default。

- [ ] **Step 5: 运行页面测试确认通过**

Run: `$env:QT_QPA_PLATFORM='offscreen'; python -m unittest tests.test_image_similarity_tool -v`

Expected: all tests `OK`。

## Task 6：主窗口和动态主题集成

**Files:**

- Modify: `tools/main.py`
- Modify: `tools/theme_utils.py`
- Modify: `tests/test_main_window.py`
- Modify: `tests/test_theme_utils.py`

- [ ] **Step 1: 写失败测试定义第 5 个标签页和主题选择器**

```python
def test_main_window_contains_image_similarity_tab(self):
    self.assertEqual(5, self.window.notebook.count())
    self.assertEqual("图片相似度检测", self.window.notebook.tabText(4))

def test_model_views_and_progress_use_dynamic_theme(self):
    stylesheet = get_global_stylesheet("missing-wallpaper.png")
    self.assertIn("QTreeView, QListView, QTableView", stylesheet)
    self.assertIn("QProgressBar", stylesheet)
```

- [ ] **Step 2: 运行测试确认预期失败**

Run: `$env:QT_QPA_PLATFORM='offscreen'; python -m unittest tests.test_main_window tests.test_theme_utils -v`

Expected: 主窗口仍为 4 个标签页，新增主题选择器不存在。

- [ ] **Step 3: 接入页面并扩展 QSS**

`main.py` 导入 `ImageSimilarityTool` 并在图片裁剪页后追加：

```python
self.notebook.addTab(ImageSimilarityTool(), "图片相似度检测")
```

同时把“四个工具页”文档字符串更新为“五个工具页”，无壁纸时仍调用 `get_global_stylesheet("")`。`theme_utils.py` 使用既有 `panel_bg`、`input_bg`、`border_color`、`text_color`、`btn_bg` 和 `nav_hover` 生成 `QTreeView/QListView/QTableView`、header、`QComboBox`、`QProgressBar`、`QPlainTextEdit`、选中与悬停样式，不增加硬编码整页背景。

- [ ] **Step 4: 运行集成测试确认通过**

Run: `$env:QT_QPA_PLATFORM='offscreen'; python -m unittest tests.test_main_window tests.test_theme_utils tests.test_image_similarity_tool -v`

Expected: all tests `OK`。

## Task 7：完整回归、文档覆盖和差异审计

**Files:**

- Verify: `tools/image_similarity/**/*.py`
- Verify: `tools/image_similarity_tool.py`
- Verify: all `tests/*.py`

- [ ] **Step 1: 运行图片相似度完整测试集**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_image_similarity_fingerprints tests.test_image_similarity_grouping tests.test_image_similarity_scanner tests.test_image_similarity_recycle_bin tests.test_image_similarity_tool tests.test_main_window tests.test_theme_utils tests.test_code_documentation -v
```

Expected: all tests `OK`，无真实回收站调用。

- [ ] **Step 2: 运行完整测试**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

Expected: all tests `OK`；两个既有无权限临时目录仅可能产生已知枚举警告，不得据此删除目录或修改业务逻辑。

- [ ] **Step 3: 静态和差异检查**

Run:

```powershell
python -m compileall -q tools
git diff --check
git status --short --branch -uall
```

Expected: Python 编译成功，`git diff --check` 无输出；差异只包含本功能、设计文档和实施计划，不包含下载产物、浏览器 profile、临时切片或来源不明文件。

- [ ] **Step 4: 代码评审**

逐项复核：扫描只读、混合阈值集中、无平方级全量比较、无链式扩组、默认无选择、确认信息完整、回收站失败不永久删除、QPixmap 只在主线程、透明背景和动态主题保持一致。发现问题先补失败测试，再修复并重跑相关与完整测试。

本计划不授权 Git 提交或推送；完成后保留工作区改动供用户检查。
