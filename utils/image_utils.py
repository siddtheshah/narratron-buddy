import os
from pathlib import Path
from typing import List, Optional
from PIL import Image

def extract_image_prompt(file_path: str) -> str:
    """Extracts prompt/description text from PNG tEXt chunks or EXIF tags."""
    prompt_text = ""
    try:
        if os.path.exists(file_path):
            with Image.open(file_path) as img:
                # 1. Try PNG tEXt chunks first
                if hasattr(img, "info") and img.info:
                    prompt_text = img.info.get("Prompt", "")
                
                # 2. Fallback to EXIF tags (0x010e for ImageDescription / 0x9c9b for XPTitle)
                if not prompt_text:
                    exif = img.getexif()
                    if exif:
                        if 0x010e in exif:
                            prompt_text = exif[0x010e]
                        elif 0x9c9b in exif:
                            val = exif[0x9c9b]
                            if isinstance(val, bytes):
                                try:
                                    prompt_text = val.decode("utf-16le").rstrip("\x00")
                                except Exception:
                                    prompt_text = str(val)
                            else:
                                prompt_text = str(val)
    except Exception:
        pass
    return prompt_text

def extract_image_metadata_description(file_path: str) -> str:
    """Extracts metadata description text from PNG tEXt chunks or EXIF tags (0x9286 / 0x9c9c)."""
    metadata_desc = ""
    try:
        if os.path.exists(file_path):
            with Image.open(file_path) as img:
                if hasattr(img, "text") and img.text:
                    metadata_desc = img.text.get("MetadataDescription", "")
                if not metadata_desc:
                    exif = img.getexif()
                    if exif:
                        if 0x9286 in exif:
                            metadata_desc = exif[0x9286]
                        elif 0x9c9c in exif:
                            val = exif[0x9c9c]
                            if isinstance(val, bytes):
                                try:
                                    metadata_desc = val.decode("utf-16le").rstrip("\x00")
                                except Exception:
                                    metadata_desc = str(val)
                            else:
                                metadata_desc = str(val)
    except Exception:
        pass
    return metadata_desc

def embed_image_metadata(exif_obj, image_prompt: str):
    """Embeds image prompt and metadata description into PIL EXIF tags."""
    if exif_obj is None:
        return
    # 0x010e: ImageDescription
    exif_obj[0x010e] = image_prompt
    # 0x9286: UserComment
    exif_obj[0x9c9b] = image_prompt.encode("utf-16le")

def compress_image_to_webp(
    image_source: Image.Image | str | Path,
    output_path: Optional[str | Path] = None,
    quality: int = 80,
    prompt: Optional[str] = None,
) -> Optional[str]:
    """Compresses an image to WebP format for fast frontend delivery.

    Args:
        image_source: A PIL Image instance or a file path (str or Path).
        output_path: Optional path to save the WebP image. If omitted and image_source is a path,
                     replaces the file extension with .webp.
        quality: WebP compression quality (default: 80).
        prompt: Optional prompt text to embed into EXIF metadata.

    Returns:
        The file path string of the saved WebP image, or None if compression failed.
    """
    try:
        if isinstance(image_source, (str, Path)):
            src_path = Path(image_source)
            if not src_path.exists() or not src_path.is_file():
                return None
            if src_path.suffix.lower() == ".webp" and output_path is None:
                return str(src_path)
            if output_path is None:
                output_path = src_path.with_suffix(".webp")

            with Image.open(src_path) as img:
                img_to_save = img.convert("RGB") if img.mode not in ("RGB", "RGBA") else img.copy()
                exif = img.getexif()
                if prompt:
                    embed_image_metadata(exif, prompt)
                out_p = Path(output_path)
                out_p.parent.mkdir(parents=True, exist_ok=True)
                img_to_save.save(out_p, "WEBP", exif=exif, quality=quality)
                return str(out_p)
        elif isinstance(image_source, Image.Image):
            if output_path is None:
                raise ValueError("output_path is required when image_source is a PIL Image.")
            img_to_save = image_source.convert("RGB") if image_source.mode not in ("RGB", "RGBA") else image_source
            exif = image_source.getexif()
            if prompt:
                embed_image_metadata(exif, prompt)
            out_p = Path(output_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            img_to_save.save(out_p, "WEBP", exif=exif, quality=quality)
            return str(out_p)
    except Exception:
        return None

