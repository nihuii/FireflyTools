import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.video_crawler.adapters.hls import HlsAdapter, build_hls_rendition_plan


def workspace_temp_dir():
    temp_root = os.path.join(os.getcwd(), "tests", ".tmp")
    os.makedirs(temp_root, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=temp_root)


class HlsRenditionPlanTests(unittest.TestCase):
    def test_selects_default_audio_and_subtitle_renditions(self):
        playlist = SimpleNamespace(
            media=[
                SimpleNamespace(
                    type="AUDIO",
                    default="YES",
                    uri="audio/main.m3u8",
                    absolute_uri="https://cdn.example.test/audio/main.m3u8",
                    name="Main",
                ),
                SimpleNamespace(
                    type="SUBTITLES",
                    default="YES",
                    uri="subs/zh.m3u8",
                    absolute_uri="https://cdn.example.test/subs/zh.m3u8",
                    name="Chinese",
                ),
            ]
        )

        plan = build_hls_rendition_plan(playlist)

        self.assertEqual(plan.audio_url, "https://cdn.example.test/audio/main.m3u8")
        self.assertEqual(plan.subtitle_url, "https://cdn.example.test/subs/zh.m3u8")


class HlsAudioMuxTests(unittest.TestCase):
    def test_muxes_default_audio_track_when_present(self):
        with workspace_temp_dir() as temp_dir:
            output_dir = os.path.join(temp_dir, "downloads")
            os.makedirs(output_dir)
            adapter = HlsAdapter(
                output_dir=output_dir,
                temp_dir=temp_dir,
                headers_getter=lambda: {},
            )

            calls = []

            async def fake_download_media_playlist(url, output_filename, suffix):
                path = os.path.join(temp_dir, f"{output_filename}.{suffix}.mp4")
                with open(path, "wb") as output:
                    output.write(suffix.encode("utf-8"))
                calls.append((url, suffix))
                return path

            with patch.object(
                adapter,
                "download_media_playlist",
                side_effect=fake_download_media_playlist,
                create=True,
            ), patch.object(adapter, "mux_renditions", create=True) as mux:
                mux.return_value = os.path.join(output_dir, "movie.mp4")

                result = adapter._run_async(
                    adapter.download_master_with_renditions(
                        video_url="https://cdn.example.test/video.m3u8",
                        audio_url="https://cdn.example.test/audio.m3u8",
                        subtitle_url=None,
                        output_filename="movie",
                    )
                )

        self.assertEqual(result, os.path.join(output_dir, "movie.mp4"))
        self.assertEqual(calls[0], ("https://cdn.example.test/video.m3u8", "video"))
        self.assertEqual(calls[1], ("https://cdn.example.test/audio.m3u8", "audio"))
        mux.assert_called_once()


class HlsSubtitleTests(unittest.TestCase):
    def test_download_subtitle_playlist_concatenates_webvtt_segments(self):
        with workspace_temp_dir() as temp_dir:
            adapter = HlsAdapter(
                output_dir=temp_dir,
                temp_dir=temp_dir,
                headers_getter=lambda: {},
            )
            playlist = SimpleNamespace(
                is_variant=False,
                segments=[
                    SimpleNamespace(absolute_uri="https://cdn/subs/1.vtt"),
                    SimpleNamespace(absolute_uri="https://cdn/subs/2.vtt"),
                ],
            )

            def fake_load(url, headers=None):
                return playlist

            def fake_get(url, headers=None, timeout=None):
                return SimpleNamespace(
                    content=f"WEBVTT\n\n{url}".encode("utf-8"),
                    raise_for_status=lambda: None,
                )

            with patch(
                "tools.video_crawler.adapters.hls.m3u8.load",
                side_effect=fake_load,
            ), patch(
                "tools.video_crawler.adapters.hls.requests.get",
                side_effect=fake_get,
            ):
                path = adapter.download_subtitle_playlist(
                    "https://cdn/subs/index.m3u8",
                    "movie",
                )

            with open(path, "r", encoding="utf-8") as subtitle_file:
                content = subtitle_file.read()
        self.assertIn("https://cdn/subs/1.vtt", content)
        self.assertIn("https://cdn/subs/2.vtt", content)


if __name__ == "__main__":
    unittest.main()
