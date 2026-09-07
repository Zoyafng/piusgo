"""Validate and store uploaded raster images, never user-supplied filenames."""
import base64
import binascii
import io
import os
import secrets
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

DIRECTORY=Path(os.getenv('PRODUCT_IMAGE_DIR',str(Path(__file__).parent/'data'/'product-images')))
class ImageUpload(BaseModel):
    model_config=ConfigDict(extra='forbid')
    content:str=Field(min_length=1,max_length=1400000)

def store_image(body):
    try:
        raw=base64.b64decode(body.content,validate=True)
        if len(raw)>1024*1024:raise ValueError()
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in ('PNG','JPEG','WEBP') or source.width*source.height>16000000:raise ValueError()
            source.load()
            output=source.convert('RGBA')
            output.thumbnail((1200,1200))
            buffer=io.BytesIO();output.save(buffer,format='WEBP',quality=88)
    except (ValueError,OSError,UnidentifiedImageError,binascii.Error,Image.DecompressionBombError):
        raise HTTPException(400,'图片无效，请使用 1 MB 以内且不超过 1600 万像素的 PNG、JPG 或 WebP')
    DIRECTORY.mkdir(parents=True,exist_ok=True)
    filename=secrets.token_hex(16)+'.webp'
    (DIRECTORY/filename).write_bytes(buffer.getvalue())
    return {'url':'/api/product-images/'+filename}
