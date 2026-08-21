# HLS 续传失败率计算修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 HLS 断点续传时错误地以本轮待下载切片数作为失败率分母的问题。

**Architecture:** 为 `HlsAdapter.download_segments()` 增加可选的完整切片总数；点播下载传入播放列表总数，其他调用保留原默认语义。合并阶段继续按磁盘实际缺失文件进行最终 3% 校验。

**Tech Stack:** Python 3、asyncio、aiohttp、m3u8、unittest

---

### Task 1: 用回归测试复现续传分母错误

**Files:**
- Modify: `tests/test_video_downloader.py`
- Test: `tests/test_video_downloader.py`

- [x] **Step 1: 写入失败测试**

在 `UniversalVideoSpiderTests` 中新增测试，构造 100 个切片，预置前 96 个
manifest 记录和本地文件，让剩余 4 个请求中的 2 个失败：

```python
def test_m3u8_resume_failure_rate_uses_full_playlist_count(self):
    with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp_dir:
        failed_urls = {
            "https://cdn.example.test/00096.ts",
            "https://cdn.example.test/00097.ts",
        }
        spider = ResumeRecordingSpider(
            output_dir=temp_dir,
            temp_dir=temp_dir,
            fail_urls=failed_urls,
        )
        video_temp_dir = os.path.join(temp_dir, "resume-rate")
        os.makedirs(video_temp_dir)

        from tools.video_crawler.resume import SegmentManifest

        manifest = SegmentManifest(
            os.path.join(video_temp_dir, ".firefly-segments.json")
        )
        for index in range(96):
            filename = f"{index:05d}.ts"
            path = os.path.join(video_temp_dir, filename)
            with open(path, "wb") as segment_file:
                segment_file.write(b"cached")
            manifest.mark_downloaded(
                filename,
                url=f"https://cdn.example.test/{filename}",
                size=os.path.getsize(path),
            )
        manifest.save()

        with patch(
            "tools.video_crawler.adapters.hls.m3u8.load",
            return_value=fake_playlist(100),
        ):
            output_path = asyncio.run(
                spider._download_m3u8(
                    "https://cdn.example.test/index.m3u8",
                    "resume-rate",
                )
            )

        self.assertEqual(len(spider.download_calls), 4)
        self.assertTrue(os.path.exists(output_path))
```

- [x] **Step 2: 运行测试并确认 RED**

Run:

```powershell
python -m unittest tests.test_video_downloader.UniversalVideoSpiderTests.test_m3u8_resume_failure_rate_uses_full_playlist_count -v
```

Expected: FAIL，并包含“有 2 个切片下载失败（总计 4 个），超过允许的 3%”。

### Task 2: 传递完整播放列表切片数

**Files:**
- Modify: `tools/video_crawler/adapters/hls.py`
- Test: `tests/test_video_downloader.py`

- [x] **Step 1: 实现最小修复**

把下载入口改为接受可选总数，并在失败率校验时优先使用它：

```python
async def download_segments(
    self,
    session,
    download_items,
    cipher,
    *,
    total_segment_count=None,
):
    ...
    failed_count = results.count(False)
    failure_rate_total = (
        total_segment_count
        if total_segment_count is not None
        else len(results)
    )
    self._validate_segment_failures(failed_count, failure_rate_total)
```

点播下载调用时传入完整列表数量：

```python
await self.download_segments(
    session,
    download_items,
    None,
    total_segment_count=len(ts_files_list),
)
```

- [x] **Step 2: 运行新增测试并确认 GREEN**

Run:

```powershell
python -m unittest tests.test_video_downloader.UniversalVideoSpiderTests.test_m3u8_resume_failure_rate_uses_full_playlist_count -v
```

Expected: PASS，并记录 2/100 未超过 3%。

- [x] **Step 3: 运行 HLS 下载相关回归**

Run:

```powershell
python -m unittest tests.test_video_downloader.UniversalVideoSpiderTests tests.test_video_crawler_hls tests.test_video_crawler_hls_live tests.test_video_crawler_hls_renditions -v
```

Expected: 全部 PASS。

### Task 3: 全量验证与工作树检查

**Files:**
- Verify: `tools/video_crawler/adapters/hls.py`
- Verify: `tests/test_video_downloader.py`

- [x] **Step 1: 运行全量测试**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -v
```

Expected: 全部 PASS。

- [x] **Step 2: 检查补丁格式和工作树**

Run:

```powershell
git diff --check
git status --short --branch -uall
```

Expected: `git diff --check` 返回 0；原有未提交文件仍在，且只新增本计划明确列出的修改。
