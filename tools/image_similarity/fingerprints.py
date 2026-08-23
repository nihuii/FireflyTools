"""计算图片 SHA-256、感知哈希、方向修正元数据及灰度与 RGBA 指纹。"""

import hashlib
from pathlib import Path
import warnings

import imagehash
from PIL import Image, ImageOps, UnidentifiedImageError

from tools.image_similarity.models import (
    ImageFingerprint,
    ImageRecord,
    ScanErrorCode,
)


GRAYSCALE_SIZE = (16, 16)
RGBA_SIZE = (8, 8)


class FingerprintError(Exception):
    """把 Pillow 和文件系统异常转换为稳定扫描错误码。"""

    def __init__(self, path: Path, code: ScanErrorCode, message: str):
        """保存失败路径、错误码和面向用户的简短说明。"""
        super().__init__(message)
        self.path = path
        self.code = code
        self.message = message


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """以固定大小数据块计算文件 SHA-256，避免整文件读入内存。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hamming_distance(first: int, second: int) -> int:
    """返回两个整数形式 64 位感知哈希的汉明距离。"""
    return (first ^ second).bit_count()


def grayscale_similarity(first: bytes, second: bytes) -> float:
    """按归一化平均绝对像素差计算灰度相似度。"""
    if len(first) != len(second) or not first:
        raise ValueError("灰度指纹长度必须相同且不能为空")
    mean_difference = sum(abs(a - b) for a, b in zip(first, second)) / len(first)
    return 1.0 - mean_difference / 255.0


def _premultiplied_rgba_bytes(image: Image.Image) -> bytes:
    """生成忽略透明像素隐藏色值、但保留透明度差异的小尺寸 RGBA 描述符。"""
    raw = image.convert("RGBA").resize(
        RGBA_SIZE,
        Image.Resampling.LANCZOS,
    ).tobytes()
    descriptor = bytearray(len(raw))
    for offset in range(0, len(raw), 4):
        alpha = raw[offset + 3]
        descriptor[offset] = round(raw[offset] * alpha / 255)
        descriptor[offset + 1] = round(raw[offset + 1] * alpha / 255)
        descriptor[offset + 2] = round(raw[offset + 2] * alpha / 255)
        descriptor[offset + 3] = alpha
    return bytes(descriptor)


def aspect_ratio_difference(first: ImageRecord, second: ImageRecord) -> float:
    """返回两个方向修正后宽高比的相对差异。"""
    largest = max(first.aspect_ratio, second.aspect_ratio)
    if largest <= 0:
        return 0.0
    return abs(first.aspect_ratio - second.aspect_ratio) / largest


def _build_fingerprint(path: Path, order: int) -> ImageFingerprint:
    """在调用方已建立异常边界时读取一张图片并生成指纹。"""
    before = path.stat()
    canonical_path = path.resolve(strict=True)

    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as opened:
            image_format = (opened.format or path.suffix.lstrip(".") or "UNKNOWN").upper()
            normalized = ImageOps.exif_transpose(opened)
            normalized.load()
            rgba_image = normalized.convert("RGBA")
            white_background = Image.new(
                "RGBA",
                rgba_image.size,
                (255, 255, 255, 255),
            )
            rgb_image = Image.alpha_composite(
                white_background,
                rgba_image,
            ).convert("RGB")
            width, height = rgb_image.size
            phash = int(str(imagehash.phash(rgb_image, hash_size=8)), 16)
            dhash = int(str(imagehash.dhash(rgb_image, hash_size=8)), 16)
            grayscale = rgb_image.convert("L").resize(
                GRAYSCALE_SIZE,
                Image.Resampling.LANCZOS,
            ).tobytes()
            rgba = _premultiplied_rgba_bytes(rgba_image)

    after = path.stat()
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
    ):
        raise FingerprintError(
            path,
            ScanErrorCode.FILE_DISAPPEARED,
            "图片在扫描期间发生变化",
        )

    record = ImageRecord(
        path=path,
        canonical_path=canonical_path,
        size_bytes=after.st_size,
        mtime_ns=after.st_mtime_ns,
        image_format=image_format,
        width=width,
        height=height,
        order=order,
        device_id=after.st_dev,
        file_id=after.st_ino,
    )
    return ImageFingerprint(
        record=record,
        phash=phash,
        dhash=dhash,
        grayscale=grayscale,
        rgba=rgba,
    )


def fingerprint_image(path: Path, order: int) -> ImageFingerprint:
    """读取图片并把常见解码或文件错误转换为结构化异常。"""
    image_path = Path(path)
    try:
        return _build_fingerprint(image_path, order)
    except FingerprintError:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise FingerprintError(
            image_path,
            ScanErrorCode.IMAGE_TOO_LARGE,
            "图片像素数量超过安全限制",
        ) from error
    except FileNotFoundError as error:
        raise FingerprintError(
            image_path,
            ScanErrorCode.FILE_DISAPPEARED,
            "图片在扫描期间消失",
        ) from error
    except PermissionError as error:
        raise FingerprintError(
            image_path,
            ScanErrorCode.PERMISSION_DENIED,
            "没有权限读取图片",
        ) from error
    except UnidentifiedImageError as error:
        raise FingerprintError(
            image_path,
            ScanErrorCode.UNSUPPORTED_IMAGE,
            "文件不是受支持的有效图片",
        ) from error
    except OSError as error:
        raise FingerprintError(
            image_path,
            ScanErrorCode.DECODE_FAILED,
            "图片解码失败",
        ) from error
    except Exception as error:
        raise FingerprintError(
            image_path,
            ScanErrorCode.UNKNOWN_SCAN_ERROR,
            "读取图片时发生未知错误",
        ) from error
