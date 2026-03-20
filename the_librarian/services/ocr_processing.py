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
from surya.detection import DetectionPredictor
from surya.recognition import RecognitionPredictor
from surya.foundation import FoundationPredictor

_det_predictor = None
_rec_predictor = None
_foundation_predictor = None

def get_predictors():
    """Lazy initialization of Surya predictors."""
    global _det_predictor, _rec_predictor, _foundation_predictor
    if _foundation_predictor is None:
        # Initializing the shared foundation predictor required by 0.17.1
        _foundation_predictor = FoundationPredictor()
        # DetectionPredictor() takes an optional string checkpoint, not a FoundationPredictor object.
        _det_predictor = DetectionPredictor()
        # RecognitionPredictor strictly requires the FoundationPredictor object.
        _rec_predictor = RecognitionPredictor(_foundation_predictor)
    return _det_predictor, _rec_predictor


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
    det_predictor, rec_predictor = get_predictors()
    try:
        # Use RecognitionPredictor directly (wrapped in a list for batch processing)
        # In Surya 0.17.1,langs are not passed here; it uses the foundation model's capabilities.
        results = rec_predictor([image])
        if results and len(results) > 0:
            output = " ".join([line.text for line in results[0].text_lines])
            return output
        return ""
    except Exception as e:
        print(f"Surya OCR failed: {e}")
        return ""

def process_image_for_ocr(yolo_image: Image.Image):
    det_predictor, rec_predictor = get_predictors()
    
    # Detect text lines first
    detection_results = det_predictor([yolo_image])
    
    # Prepare bboxes for batch recognition
    # We add a small padding (5px) to each box to ensure characters aren't clipped
    bboxes = []
    width, height = yolo_image.size
    for r in detection_results:
        per_image_bboxes = []
        for b in r.bboxes:
            # Padding
            pad = 5
            x1, y1, x2, y2 = b.bbox
            x1 = max(0, x1 - pad)
            y1 = max(0, y1 - pad)
            x2 = min(width, x2 + pad)
            y2 = min(height, y2 + pad)
            per_image_bboxes.append([x1, y1, x2, y2])
        bboxes.append(per_image_bboxes)
    
    # Batch recognition for all detected lines
    # bboxes is List[List[List[int]]] matching the [yolo_image] list
    results = rec_predictor([yolo_image], bboxes=bboxes)
    
    # Join text lines with newlines
    extracted_text = "\n".join([line.text for line in results[0].text_lines])
    
    return extracted_text.strip()
