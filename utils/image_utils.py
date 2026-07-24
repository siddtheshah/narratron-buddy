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

def embed_image_metadata(exif_obj, image_prompt: str, metadata_description: str):
    """Embeds image prompt and metadata description into PIL EXIF tags."""
    if exif_obj is None:
        return
    # 0x010e: ImageDescription
    exif_obj[0x010e] = image_prompt
    # 0x9286: UserComment
    exif_obj[0x9286] = metadata_description
    # 0x9c9b: XPTitle
    exif_obj[0x9c9b] = image_prompt.encode("utf-16le")
    # 0x9c9c: XPComment
    exif_obj[0x9c9c] = metadata_description.encode("utf-16le")

def resolve_image_path(path_str: str, candidate_dirs: Optional[List[str]] = None) -> Optional[str]:
    """Resolves an image filename, path, or alias against direct existence and candidate directories."""
    if not path_str:
        return None
    
    if os.path.exists(path_str):
        return path_str

    base_name = os.path.basename(path_str)
    root_dir = Path(__file__).parent.parent.resolve()

    default_dirs = [
        str(root_dir / "testing" / "testdata" / "images"),
        str(root_dir / "testing" / "testdata"),
    ]

    search_dirs = candidate_dirs or []
    for d in default_dirs:
        if d not in search_dirs:
            search_dirs.append(d)

    for directory in search_dirs:
        candidates = [
            os.path.join(directory, path_str),
            os.path.join(directory, base_name),
            os.path.join(directory, f"{path_str}.jpg"),
            os.path.join(directory, f"{path_str}.png"),
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate

    return None
