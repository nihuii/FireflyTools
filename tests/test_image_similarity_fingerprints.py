"""验证图片相似度功能的数据模型和混合指纹。"""

from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image, ImageDraw

from tools.image_similarity.fingerprints import (
    fingerprint_image,
    grayscale_similarity,
    hamming_distance,
    sha256_file,
)
from tools.image_similarity.grouping import is_visually_similar
from tools.image_similarity.models import SimilarityPreset, thresholds_for


class ImageSimilarityFingerprintTests(unittest.TestCase):
    """使用合成图片验证指纹计算，不依赖用户文件。"""

    def setUp(self):
        """创建一次性目录供每个测试写入合成图片。"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        """清理当前测试创建的一次性图片。"""
        self.temp_dir.cleanup()

    def _pattern_image(self, size=(96, 64)):
        """生成同时包含轮廓、色块和细节的非对称测试图。"""
        image = Image.new("RGB", size, "#f2e8cf")
        draw = ImageDraw.Draw(image)
        draw.rectangle((5, 7, 45, 35), fill="#1d3557")
        draw.ellipse((52, 14, 88, 50), fill="#e63946")
        draw.line((0, size[1] - 3, size[0], 3), fill="#2a9d8f", width=4)
        return image

    def test_presets_have_monotonic_thresholds(self):
        """越宽松的预设扩大距离并降低灰度相似度下限。"""
        strict = thresholds_for(SimilarityPreset.STRICT)
        standard = thresholds_for(SimilarityPreset.STANDARD)
        loose = thresholds_for(SimilarityPreset.LOOSE)

        self.assertLessEqual(strict.phash_distance, standard.phash_distance)
        self.assertLessEqual(standard.phash_distance, loose.phash_distance)
        self.assertLessEqual(strict.dhash_distance, standard.dhash_distance)
        self.assertLessEqual(standard.dhash_distance, loose.dhash_distance)
        self.assertGreaterEqual(
            strict.grayscale_similarity,
            standard.grayscale_similarity,
        )
        self.assertGreaterEqual(
            standard.grayscale_similarity,
            loose.grayscale_similarity,
        )

    def test_same_bytes_have_same_sha256(self):
        """内容完全相同而文件名不同的图片得到相同 SHA-256。"""
        first = self.root / "first.png"
        second = self.root / "renamed.png"
        self._pattern_image().save(first)
        shutil.copyfile(first, second)

        self.assertEqual(sha256_file(first), sha256_file(second))

    def test_png_and_jpeg_versions_have_close_visual_fingerprints(self):
        """格式转换和有损压缩后的同图仍保持接近的视觉指纹。"""
        png_path = self.root / "source.png"
        jpeg_path = self.root / "compressed.jpg"
        image = self._pattern_image()
        image.save(png_path)
        image.resize((144, 96)).save(jpeg_path, quality=88)

        png_fingerprint = fingerprint_image(png_path, order=0)
        jpeg_fingerprint = fingerprint_image(jpeg_path, order=1)

        self.assertLessEqual(
            hamming_distance(png_fingerprint.phash, jpeg_fingerprint.phash),
            4,
        )
        self.assertLessEqual(
            hamming_distance(png_fingerprint.dhash, jpeg_fingerprint.dhash),
            4,
        )
        self.assertGreaterEqual(
            grayscale_similarity(
                png_fingerprint.grayscale,
                jpeg_fingerprint.grayscale,
            ),
            0.94,
        )

    def test_visual_fingerprint_applies_exif_orientation(self):
        """EXIF 方向不同但显示内容一致的图片使用相同方向计算。"""
        oriented_path = self.root / "oriented.jpg"
        rotated_path = self.root / "rotated.png"
        source = self._pattern_image((80, 48))
        exif = Image.Exif()
        exif[274] = 6
        source.save(oriented_path, quality=96, exif=exif)
        source.transpose(Image.Transpose.ROTATE_270).save(rotated_path)

        oriented = fingerprint_image(oriented_path, order=0)
        rotated = fingerprint_image(rotated_path, order=1)

        self.assertEqual(oriented.record.dimensions, rotated.record.dimensions)
        self.assertLessEqual(hamming_distance(oriented.phash, rotated.phash), 4)
        self.assertLessEqual(hamming_distance(oriented.dhash, rotated.dhash), 4)

    def test_grayscale_similarity_uses_normalized_mean_difference(self):
        """灰度复核严格采用设计文档定义的归一化平均差。"""
        self.assertAlmostEqual(
            1.0 - ((0 + 127 + 255) / 3) / 255,
            grayscale_similarity(bytes((0, 0, 0)), bytes((0, 127, 255))),
        )

    def test_transparent_and_opaque_images_are_not_visually_similar(self):
        """透明度明显不同的图片不能因 RGB 相同被严格模式归组。"""
        transparent_path = self.root / "transparent.png"
        opaque_path = self.root / "opaque.png"
        Image.new("RGBA", (64, 64), (255, 0, 0, 0)).save(transparent_path)
        Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(opaque_path)

        transparent = fingerprint_image(transparent_path, order=0)
        opaque = fingerprint_image(opaque_path, order=1)

        self.assertFalse(
            is_visually_similar(
                transparent,
                opaque,
                thresholds_for(SimilarityPreset.STRICT),
            )
        )

    def test_equal_luminance_different_colors_are_not_visually_similar(self):
        """等亮度但色相明显不同的纯色图片仍需通过色度复核。"""
        red_path = self.root / "red.png"
        green_path = self.root / "green.png"
        Image.new("RGB", (64, 64), (255, 0, 0)).save(red_path)
        Image.new("RGB", (64, 64), (0, 130, 0)).save(green_path)

        red = fingerprint_image(red_path, order=0)
        green = fingerprint_image(green_path, order=1)

        self.assertEqual(256, len(red.rgba))
        self.assertEqual(256, len(green.rgba))
        self.assertEqual(red.grayscale, green.grayscale)
        self.assertFalse(
            is_visually_similar(
                red,
                green,
                thresholds_for(SimilarityPreset.STRICT),
            )
        )


if __name__ == "__main__":
    unittest.main()
