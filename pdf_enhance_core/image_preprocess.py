import cv2
import numpy as np
from PIL import Image

def enhance_image_for_ocr(cv_img: np.ndarray) -> np.ndarray:
    """
    自适应对比度增强与清晰化，提升低对比度文字的识别率。
    严格保持原图尺寸与坐标系，不进行任何破坏坐标对应关系的旋转操作。
    """
    try:
        if len(cv_img.shape) == 3:
            lab = cv2.cvtColor(cv_img, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
            cl = clahe.apply(l)
            limg = cv2.merge((cl, a, b))
            return cv2.cvtColor(limg, cv2.COLOR_LAB2BGR)
        else:
            clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
            return clahe.apply(cv_img)
    except Exception:
        return cv_img

def preprocess_page_image(pil_img: Image.Image) -> np.ndarray:
    """图像预处理流水线：保持 1:1 像素坐标系绝对一致"""
    cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    cv_img = enhance_image_for_ocr(cv_img)
    return cv_img
