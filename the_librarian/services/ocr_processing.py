import torch
import cv2
import numpy as np
from PIL import Image

# --- scikit-image compatibility imports ---
try:
    # Newer versions (>=0.25)
    from skimage.feature._canny import canny
except ImportError:
    # Older versions (<0.25)
    from skimage.feature import canny

try:
    from skimage.transform import rotate
    from skimage.transform._hough_transform import hough_line, hough_line_peaks
except ImportError:
    from skimage.transform import hough_line, hough_line_peaks, rotate

from scipy.stats import mode
from surya.ocr import run_ocr
from surya.model.detection import segformer
from surya.model.recognition.model import load_model
from surya.model.recognition.processor import load_processor, SuryaProcessor
from surya.input.processing import slice_polys_from_image
from surya.detection import batch_text_detection


# Use CPU for compatibility
device = "cpu"
langs = ["ml", "en"]

det_processor, det_model = segformer.load_processor(), segformer.load_model(
    device=device, dtype=torch.float32 if device == "cpu" else torch.float16
)

rec_model, rec_processor = (
    load_model(device=device, dtype=torch.float32 if device == "cpu" else torch.float16),
    load_processor(),
)

# Manual grayscale conversion (instead of deprecated skimage.rgb2gray)
def rgb2gray_manual(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:  # already grayscale
        return image
    elif image.ndim == 3 and image.shape[2] == 3:
        return np.dot(image[..., :3], [0.2989, 0.5870, 0.1140])
    else:
        raise ValueError(f"Unsupported image shape for grayscale conversion: {image.shape}")

def get_skew_corrected_image(image: np.ndarray) -> np.ndarray:
    try:
        # Downscale very large images to avoid memory issues
        max_dim = max(image.shape[0], image.shape[1])
        if max_dim > 2000:
            scale = 2000.0 / max_dim
            new_h, new_w = int(image.shape[0] * scale), int(image.shape[1] * scale)
            image_small = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)  # type: ignore
        else:
            image_small = image

        grey_image = rgb2gray_manual(image_small)
        edges = canny(grey_image)
        tested_angles = np.deg2rad(np.arange(0.1, 180.0))
        h, theta, dist = hough_line(edges, theta=tested_angles)
        _, angles, _ = hough_line_peaks(h, theta, dist)

        if angles.size == 0:
            return image

        m = mode(np.around(angles, decimals=2))
        if hasattr(m, "mode"):   # SciPy >=1.9
            most_common_angle = m.mode
            if isinstance(most_common_angle, np.ndarray):
                most_common_angle = most_common_angle[0]
        else:  # SciPy <1.9
            most_common_angle = m[0]

        skew_angle = np.rad2deg(most_common_angle - np.pi / 2)

        corrected = (rotate(image, skew_angle, cval=1) * 255).astype(np.uint8)
        return corrected

    except Exception as e:
        print(f"Skew correction failed: {e}")
        return image


def surya_ocr(image):
    torch.cuda.empty_cache()
    try:
        predictions = run_ocr(
            [image], [langs], det_model, det_processor, rec_model, rec_processor
        )
        output = " ".join([p.text for p in predictions[0].text_lines])
        predictions = None
        return output
    except Exception as e:
        print(f"Surya OCR failed: {e}")
        return ""

def process_image_for_ocr(yolo_image: Image.Image):
    detection_results = batch_text_detection([yolo_image], det_model, det_processor)
    detection_bboxes = [r.bbox for r in detection_results[0].bboxes]

    extracted_text = ""
    for detection_index, bbox in enumerate(detection_bboxes):
        height, _, __ = np.array(yolo_image).shape
        y1, y2 = bbox[1], bbox[3]

        pad = 10
        y1_padded = max(0, int(y1 - pad))
        y2_padded = min(height, int(y2 + pad))

        line_image_np = np.array(yolo_image)[
            y1_padded:y2_padded,
            int(bbox[0]):int(bbox[2]),
        ]

        if line_image_np.size == 0:
            continue

        line_image = Image.fromarray(line_image_np)
        text = surya_ocr(line_image)
        extracted_text += text + "\n"

    return extracted_text.strip()
