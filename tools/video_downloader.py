import os
import threading
import queue
import asyncio
import aiohttp
import requests
import m3u8
import subprocess
import random
from urllib.parse import urlparse

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QListWidget, QTextEdit, QFileDialog, QMessageBox, QFrame)
from PyQt6.QtCore import pyqtSignal, Qt

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from playwright.sync_api import sync_playwright

from tools.theme_utils import apply_shadow


# ==========================================
# 核心爬虫业务逻辑层 (严格对齐最新 fMP4+防广告 逻辑)
# ==========================================
class UniversalVideoSpider:
    def __init__(self, output_dir="./downloads", temp_dir="./temp", log_callback=None, is_high_speed=False):
        self.output_dir = output_dir
        self.temp_dir = temp_dir
        self.log_callback = log_callback
        self.is_high_speed = is_high_speed
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)

    def log(self, message):
        if self.log_callback:
            self.log_callback(message)
        else:
            print(message)

    def run(self, url: str, output_filename: str):
        mode_str = "【高速模式】" if self.is_high_speed else "【低速稳定模式】"
        self.log(f"[*] 开始分析目标 URL ({mode_str}): {url}")

        if url.lower().endswith('.mp4') or '.mp4?' in url:
            self.log("[*] 判定为直链 MP4，启动普通下载模块...")
            save_path = os.path.join(self.output_dir, f"{output_filename}.mp4")
            self._download_mp4(url, save_path)

        elif url.lower().endswith('.m3u8') or '.m3u8?' in url:
            self.log("[*] 判定为 M3U8 流，启动异步切片下载模块...")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._download_m3u8(url, output_filename))
            loop.close()

        else:
            self.log("[*] 判定为网页，启动 Playwright 嗅探真实视频流 (请耐心等待浏览器后台加载)...")
            real_url = self._sniff_real_url(url)
            if real_url:
                self.log(f"[+] 嗅探成功，真实地址为: {real_url}")
                self.headers["Referer"] = url
                parsed_url = urlparse(url)
                self.headers["Origin"] = f"{parsed_url.scheme}://{parsed_url.netloc}"
                self.run(real_url, output_filename)
            else:
                self.log("[-] 嗅探失败，未能找到视频流地址或受到严重混淆。")

    # 【全新防广告机制】深度探测每个 M3U8 候选者的切片数量
    def _select_best_m3u8(self, m3u8_urls):
        unique_urls = []
        for u in m3u8_urls:
            if u not in unique_urls:
                unique_urls.append(u)

        best_url = None
        max_segments = -1

        self.log(f"[*] 开始对 {len(unique_urls)} 个候选流进行「切片数量深度探测」以过滤广告...")

        for u in unique_urls:
            try:
                # 给个很短的超时时间快速试探
                res = requests.get(u, headers=self.headers, timeout=5)
                res.raise_for_status()

                # 防御性过滤：确认是真正的 m3u8 文本
                if "#EXTM3U" not in res.text:
                    continue

                playlist = m3u8.loads(res.text, uri=u)

                # 如果是主列表，随便挑一个子列表去探测切片数量即可
                if playlist.is_variant:
                    sub_url = playlist.playlists[0].absolute_uri
                    sub_res = requests.get(sub_url, headers=self.headers, timeout=5)
                    sub_playlist = m3u8.loads(sub_res.text, uri=sub_url)
                    seg_count = len(sub_playlist.segments)
                else:
                    seg_count = len(playlist.segments)

                self.log(f"  -> 探测完毕 | 切片数: {seg_count:4d} | 链接: {u[:60]}...")

                if seg_count > max_segments:
                    max_segments = seg_count
                    best_url = u

            except Exception as e:
                pass  # 忽略探测失败的死链

        return best_url, max_segments

    def _sniff_real_url(self, page_url: str) -> str:
        found_m3u8 = []
        found_mp4 = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--mute-audio'])
            page = browser.new_page()

            def handle_response(response):
                try:
                    url = response.url.lower()
                    # 扩大了广告黑名单关键词，先在表层过滤掉一部分低级广告
                    if any(x in url for x in ["ad.", "/ad/", "adv", "blank", "test", "preview", "v.admaster"]):
                        return

                    content_type = response.headers.get("content-type", "").lower()
                    if ".m3u8" in url or "mpegurl" in content_type:
                        self.log(f"[*] 嗅探到 M3U8 候选流: {response.url[:60]}...")
                        found_m3u8.append(response.url)
                    elif ".mp4" in url or "video/mp4" in content_type:
                        found_mp4.append(response.url)
                except Exception:
                    pass

            page.on("response", handle_response)

            try:
                page.goto(page_url, wait_until="networkidle", timeout=25000)
                self.log("[*] 正在尝试模拟点击播放器以触发真实数据流...")
                try:
                    video_element = page.locator("video").first
                    video_element.click(timeout=3000)
                    page.wait_for_timeout(3000)
                except Exception:
                    try:
                        page.mouse.click(page.viewport_size['width'] / 2, page.viewport_size['height'] / 2)
                        page.wait_for_timeout(3000)
                    except Exception:
                        pass
            except Exception as e:
                self.log(f"[!] 页面加载异常或超时 (正常拦截): {e}")
            finally:
                browser.close()

        if found_m3u8:
            # 启动决战：用切片数量来决定谁是真李逵，谁是广告假李鬼
            best_url, seg_count = self._select_best_m3u8(found_m3u8)

            if best_url:
                if seg_count < 10:
                    self.log(f"[!] 警告: 选出的最佳 M3U8 切片数依然极少 ({seg_count} 个)，可能全是短视频/广告。")
                else:
                    self.log(f"[+] 决策结果: 成功锁定正片流！该流拥有最多切片 ({seg_count} 个)。")
                return best_url
            else:
                final_url = found_m3u8[-1]
                self.log("[+] 决策结果: 深度探测未果，降级使用最后捕获的 M3U8 流。")
                return final_url

        elif found_mp4:
            final_url = found_mp4[-1]
            self.log("[+] 决策结果: 未发现 M3U8，降级使用捕获到的 MP4。")
            return final_url

        return None

    def _download_mp4(self, video_url: str, save_path: str):
        with requests.get(video_url, headers=self.headers, stream=True) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            downloaded = 0
            last_percent = 0

            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = int((downloaded / total_size) * 100)
                            if percent - last_percent >= 10:
                                self.log(f"[>] MP4 下载进度: {percent}%")
                                last_percent = percent
        self.log(f"[+] MP4 下载完成: {save_path}")

    async def _download_ts(self, session, ts_url, save_path, cipher):
        retries = 3 if self.is_high_speed else 5
        for attempt in range(retries):
            try:
                if not self.is_high_speed:
                    await asyncio.sleep(random.uniform(0.1, 0.6))

                timeout_val = 15 if self.is_high_speed else 20
                timeout = aiohttp.ClientTimeout(total=timeout_val)

                async with session.get(ts_url, timeout=timeout) as response:
                    response.raise_for_status()
                    content = await response.read()
                    if cipher:
                        decryptor = cipher.decryptor()
                        content = decryptor.update(content) + decryptor.finalize()
                    with open(save_path, 'wb') as f:
                        f.write(content)
                    return
            except Exception as e:
                if attempt == retries - 1:
                    self.log(f"[!] 切片 {ts_url[-10:]} 彻底失败 (已重试{retries}次): {e}")
                else:
                    if self.is_high_speed:
                        await asyncio.sleep(1)
                    else:
                        wait_time = (attempt + 1) * 2
                        if "503" in str(e) or "429" in str(e):
                            wait_time += 3
                        await asyncio.sleep(wait_time)

    async def _download_m3u8(self, m3u8_url: str, output_filename: str):
        playlist = m3u8.load(m3u8_url, headers=self.headers)

        if playlist.is_variant:
            playlists = list(playlist.playlists)
            playlists.sort(key=lambda p: p.stream_info.bandwidth if p.stream_info.bandwidth else 0, reverse=True)
            m3u8_url = playlists[0].absolute_uri
            self.log(f"[*] 检测到多画质变体，已自动选择最高画质流...")
            playlist = m3u8.load(m3u8_url, headers=self.headers)

        cipher = None
        if playlist.keys and playlist.keys[0]:
            key_url = playlist.keys[0].absolute_uri
            key = requests.get(key_url, headers=self.headers).content
            iv = playlist.keys[0].iv
            iv = bytes.fromhex(iv[2:]) if iv else b'\x00' * 16
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
            self.log("[+] 检测到加密，已加载 AES 引擎。")

        video_temp_dir = os.path.join(self.temp_dir, output_filename)
        os.makedirs(video_temp_dir, exist_ok=True)

        init_file_path = None
        if playlist.segment_map:
            self.log("[+] 检测到 fMP4 格式，正在下载 Init 初始化文件 (EXT-X-MAP)...")
            init_url = playlist.segment_map[0].absolute_uri
            init_file_path = os.path.join(video_temp_dir, "init.mp4")
            try:
                res = requests.get(init_url, headers=self.headers, timeout=15)
                res.raise_for_status()
                with open(init_file_path, 'wb') as f:
                    f.write(res.content)
            except Exception as e:
                self.log(f"[-] Init 文件下载失败，合并可能会发生错误: {e}")
                init_file_path = None

        ts_files_list = []
        self.log(f"[*] 共发现 {len(playlist.segments)} 个数据切片，准备下载...")

        async with aiohttp.ClientSession(headers=self.headers, trust_env=True) as session:
            tasks = []
            for i, segment in enumerate(playlist.segments):
                ts_url = segment.absolute_uri
                ts_name = f"{i:05d}.ts"
                save_path = os.path.join(video_temp_dir, ts_name)
                ts_files_list.append(save_path)
                tasks.append(self._download_ts(session, ts_url, save_path, cipher))

            concurrent_limit = 30 if self.is_high_speed else 5
            self.log(f"[*] 当前并发数限制设为: {concurrent_limit}")
            sem = asyncio.Semaphore(concurrent_limit)

            async def bound_task(t):
                async with sem:
                    await t

            await asyncio.gather(*(bound_task(t) for t in tasks))

        self.log("[+] 切片处理完成，开始执行合并与转码...")
        final_mp4_path = os.path.join(self.output_dir, f"{output_filename}.mp4")

        self._merge_with_ffmpeg(ts_files_list, final_mp4_path, init_file_path)

        for f in ts_files_list:
            if os.path.exists(f):
                os.remove(f)
        if init_file_path and os.path.exists(init_file_path):
            os.remove(init_file_path)
        os.rmdir(video_temp_dir)
        self.log("[+] 临时文件已清理，任务彻底完成！")

    def _merge_with_ffmpeg(self, ts_files: list, output_mp4: str, init_file: str = None):
        valid_ts_files = [ts for ts in ts_files if os.path.exists(ts)]

        if not valid_ts_files:
            self.log("[-] 没有任何有效的切片被下载，合并任务中止！")
            return

        if len(valid_ts_files) < len(ts_files):
            self.log(f"[!] 警告: 有 {len(ts_files) - len(valid_ts_files)} 个切片缺失。仍将强行合并。")

        if init_file and os.path.exists(init_file):
            self.log("[*] 正在执行 fMP4 底层二进制重组，请稍候...")
            raw_mp4 = os.path.join(self.temp_dir, "raw_merged.mp4")
            try:
                with open(raw_mp4, 'wb') as outfile:
                    with open(init_file, 'rb') as infile:
                        outfile.write(infile.read())
                    for ts in valid_ts_files:
                        with open(ts, 'rb') as infile:
                            outfile.write(infile.read())

                self.log("[*] 二进制拼接完成，正在使用 FFmpeg 修复媒体容器...")
                command = ['ffmpeg', '-y', '-i', raw_mp4, '-c', 'copy', output_mp4]
                startupinfo = None
                if os.name == 'nt':
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
                               startupinfo=startupinfo)

                self.log(f"[+] fMP4 视频成功产出并保存至:\n {output_mp4}")
            except subprocess.CalledProcessError as e:
                self.log(f"[-] FFmpeg 修复容器失败！错误:\n{e.stderr.decode('utf-8', errors='ignore')}")
            except Exception as e:
                self.log(f"[-] 文件读写异常: {e}")
            finally:
                if os.path.exists(raw_mp4):
                    os.remove(raw_mp4)
            return

        list_file_path = os.path.join(self.temp_dir, "concat_list.txt")
        with open(list_file_path, 'w', encoding='utf-8') as f:
            for ts in valid_ts_files:
                abs_path = os.path.abspath(ts).replace('\\', '/')
                f.write(f"file '{abs_path}'\n")

        command = [
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
            '-i', list_file_path, '-c', 'copy', '-bsf:a', 'aac_adtstoasc', output_mp4
        ]
        try:
            self.log("[*] 正在执行 FFmpeg 传统无损合并，请稍候...")
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, startupinfo=startupinfo)
            self.log(f"[+] 视频成功产出并保存至:\n {output_mp4}")
        except subprocess.CalledProcessError as e:
            self.log(f"[-] FFmpeg 合并失败！请检查系统环境。错误:\n{e.stderr.decode('utf-8', errors='ignore')}")
        finally:
            if os.path.exists(list_file_path):
                os.remove(list_file_path)


# ==========================================
# 图形用户界面 (GUI) 层
# ==========================================
class VideoDownloaderTool(QWidget):
    log_signal = pyqtSignal(str)
    queue_pop_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.task_queue = queue.Queue()
        self.is_high_speed_mode = False  # 默认使用低速稳定模式

        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.container = QFrame()
        self.container.setObjectName("container")
        self.container.setFixedWidth(650)
        apply_shadow(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(15)
        main_layout.addWidget(self.container)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("目标网址:"))
        self.url_entry = QLineEdit()
        row1.addWidget(self.url_entry)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("保存名称:"))
        self.name_entry = QLineEdit("my_video_01")
        row2.addWidget(self.name_entry)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("保存位置:"))
        self.path_entry = QLineEdit(os.path.abspath("./downloads"))
        row3.addWidget(self.path_entry)
        btn_browse = QPushButton("浏览...")
        btn_browse.clicked.connect(self.select_folder)
        row3.addWidget(btn_browse)
        layout.addLayout(row3)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        # 模式切换按钮 (保留了无硬编码颜色的 Emoji 主题自适应版本)
        self.mode_btn = QPushButton("🛡️ 当前: 低速稳定模式")
        self.mode_btn.clicked.connect(self.toggle_mode)
        btn_layout.addWidget(self.mode_btn)

        self.add_btn = QPushButton("➕ 添加到下载队列")
        self.add_btn.clicked.connect(self.add_to_queue)
        btn_layout.addWidget(self.add_btn)
        layout.addLayout(btn_layout)

        layout.addWidget(QLabel("等待队列:"))
        self.queue_listbox = QListWidget()
        self.queue_listbox.setMaximumHeight(80)
        layout.addWidget(self.queue_listbox)

        layout.addWidget(QLabel("运行日志:"))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        layout.addWidget(self.log_text)

        self.log_signal.connect(self.append_log)
        self.queue_pop_signal.connect(self.pop_queue_ui)

        self.log_signal.emit("欢迎使用视频爬虫工具！等待添加任务...\n")

        threading.Thread(target=self.queue_worker, daemon=True).start()

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择保存位置")
        if folder: self.path_entry.setText(folder)

    def append_log(self, message):
        self.log_text.append(message)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def pop_queue_ui(self):
        if self.queue_listbox.count() > 0:
            self.queue_listbox.takeItem(0)

    def toggle_mode(self):
        self.is_high_speed_mode = not self.is_high_speed_mode
        if self.is_high_speed_mode:
            self.mode_btn.setText("⚡ 当前: 高速爆发模式")
            self.log_signal.emit("[!] 已切换至【高速爆发模式】: 速度极快，但容易遇到 503 报错导致丢切片。")
        else:
            self.mode_btn.setText("🛡️ 当前: 低速稳定模式")
            self.log_signal.emit("[*] 已切换至【低速稳定模式】: 带防封禁和退避重试，保证视频完整性。")

    def add_to_queue(self):
        url, name, save_dir = self.url_entry.text().strip(), self.name_entry.text().strip(), self.path_entry.text().strip()
        if not all([url, name, save_dir]):
            QMessageBox.warning(self, "警告", "请填写完整信息！")
            return

        task = {
            "url": url,
            "name": name,
            "save_dir": save_dir,
            "is_high_speed": self.is_high_speed_mode
        }
        self.task_queue.put(task)

        mode_label = "高速" if self.is_high_speed_mode else "稳定"
        display_text = f"[{mode_label}] {name} -> {url[:40]}..."
        self.queue_listbox.addItem(display_text)
        self.log_signal.emit(f"[+] 已添加队列 ({mode_label}模式): {name}")
        self.url_entry.clear()

    def queue_worker(self):
        while True:
            task = self.task_queue.get()
            self.queue_pop_signal.emit()

            url = task['url']
            name = task['name']
            save_dir = task['save_dir']
            is_high_speed = task.get('is_high_speed', False)

            self.log_signal.emit("\n" + "=" * 50)
            self.log_signal.emit(f"▶ 开始执行: {name}")
            try:
                spider = UniversalVideoSpider(
                    output_dir=save_dir,
                    temp_dir="./temp",
                    log_callback=self.log_signal.emit,
                    is_high_speed=is_high_speed
                )
                spider.run(url, name)
            except Exception as e:
                self.log_signal.emit(f"\n[X] 错误: {e}")
            finally:
                self.log_signal.emit(f"⏹ 任务 {name} 结束。等待下一个任务...\n")
                self.task_queue.task_done()