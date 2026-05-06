import torch
import cv2
import numpy as np
from PIL import Image
import os
import logging

# Force CPU for all OCR processing
os.environ.setdefault("TORCH_DEVICE", "cpu")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

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

logger = logging.getLogger(__name__)

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


def preprocess_image(img_np: np.ndarray) -> np.ndarray:
    """
    Preprocess image for better OCR accuracy on scanned documents.
    Applies CLAHE contrast enhancement in LAB color space and light denoising.
    """
    if img_np.ndim == 2:
        # Grayscale input — apply CLAHE directly
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(img_np)
        denoised = cv2.fastNlMeansDenoising(enhanced, h=10)
        return cv2.cvtColor(denoised, cv2.COLOR_GRAY2RGB)

    if img_np.ndim == 3 and img_np.shape[2] == 3:
        # Apply CLAHE to the L channel in LAB color space (preserves color)
        lab = cv2.cvtColor(img_np, cv2.COLOR_RGB2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l_enhanced = clahe.apply(l_channel)

        lab_enhanced = cv2.merge([l_enhanced, a_channel, b_channel])
        result = cv2.cvtColor(lab_enhanced, cv2.COLOR_LAB2RGB)

        # Light denoising
        result = cv2.fastNlMeansDenoisingColored(result, None, 10, 10)

        return result

    return img_np


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

def process_image_for_ocr(yolo_image: Image.Image, confidence_threshold: float = 0.3):
    """
    OCR a single image region using Surya detection + recognition.
    Filters out low-confidence text lines to reduce OCR noise.

    Args:
        yolo_image: PIL Image of the text region
        confidence_threshold: minimum confidence to keep a line (0.0–1.0)
    """
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

    if not bboxes or not bboxes[0]:
        # No text lines detected — fall back to recognition without detection
        return surya_ocr(yolo_image)
    
    # Batch recognition for all detected lines
    # bboxes is List[List[List[int]]] matching the [yolo_image] list
    results = rec_predictor([yolo_image], bboxes=bboxes)
    
    # Filter by confidence and join text lines
    lines = []
    for line in results[0].text_lines:
        conf = getattr(line, 'confidence', 1.0)
        if conf < confidence_threshold:
            logger.debug(
                f"Low-confidence OCR line dropped (conf={conf:.2f}): "
                f"{line.text[:60]!r}"
            )
            continue
        lines.append(line.text)
    
    return "\n".join(lines).strip()


def process_page(pil_image: Image.Image, confidence_threshold: float = 0.3) -> str:
    """
    Full OCR pipeline for a single PDF page.

    Pipeline: per-page skew correction → YOLO segmentation → column-aware
              sorting → image preprocessing → Surya OCR with confidence filtering

    Args:
        pil_image: PIL Image of the full page (at 300 DPI)
        confidence_threshold: minimum confidence to keep a text line (0.0–1.0)

    Returns:
        Extracted text from the page
    """
    from the_librarian.services.yolo_segmentation import get_masks, custom_sort

    img_np = np.array(pil_image)

    # Step 1: Detect and correct skew on the full page (once, not per-crop)
    corrected_page = get_skew_corrected_image(img_np)
    corrected_pil = Image.fromarray(corrected_page)

    # Step 2: YOLO segmentation to find text regions
    response = get_masks(corrected_page)

    if response["status"] <= 0 or "boxes1" not in response or response["boxes1"].size == 0:
        # Fallback: OCR the full page without segmentation
        logger.info("  YOLO found no text regions — OCR-ing full page as fallback")
        preprocessed = preprocess_image(corrected_page)
        preprocessed_pil = Image.fromarray(preprocessed)
        return process_image_for_ocr(preprocessed_pil, confidence_threshold)

    # Step 3: Column-aware reading order sort
    boxes = custom_sort(
        response["boxes1"],
        image_height=corrected_page.shape[0],
        page_width=corrected_page.shape[1],
    )

    # Step 4: OCR each text region with preprocessing
    page_texts = []
    for box in boxes:
        x1, y1, x2, y2 = box[:4]
        crop = corrected_page[y1:y2, x1:x2]

        if crop.size == 0:
            continue

        # Preprocess the crop for better OCR (CLAHE + denoising)
        preprocessed = preprocess_image(crop)
        crop_pil = Image.fromarray(preprocessed)

        text = process_image_for_ocr(crop_pil, confidence_threshold)
        if text.strip():
            page_texts.append(text)

    return "\n".join(page_texts)
