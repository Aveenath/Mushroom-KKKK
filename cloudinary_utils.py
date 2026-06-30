import os
import cloudinary
import cloudinary.uploader
import numpy as np
import cv2
import io
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)


def upload_pil_image(pil_image: Image.Image, public_id: str) -> str:
    """Upload a PIL image (original photo) to Cloudinary. Returns secure URL."""
    buffer = io.BytesIO()
    pil_image.save(buffer, format="JPEG", quality=85)
    buffer.seek(0)
    result = cloudinary.uploader.upload(
        buffer,
        public_id=public_id,
        folder="mushroom/originals",
        overwrite=True,
        resource_type="image"
    )
    return result["secure_url"]


def upload_numpy_image(np_image: np.ndarray, public_id: str) -> str:
    """Upload a numpy BGR image (YOLO annotated result) to Cloudinary. Returns secure URL."""
    img_rgb = cv2.cvtColor(np_image, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(img_rgb)
    buffer = io.BytesIO()
    pil_img.save(buffer, format="JPEG", quality=85)
    buffer.seek(0)
    result = cloudinary.uploader.upload(
        buffer,
        public_id=public_id,
        folder="mushroom/analyzed",
        overwrite=True,
        resource_type="image"
    )
    return result["secure_url"]