import os
import numpy as np
from ultralytics import YOLO
from PIL import Image

device = "cpu"

model_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "model")
general_model_name = "e50_aug.pt"
image_model_name = "e100_img.pt"

# --- #2: Lazy model loading ---
# Models are no longer loaded at import time. Any process that imports this
# module (tests, management commands, the admin) will NOT trigger a GPU/CPU
# model load. get_models() loads and caches them on first actual use.
_general_model = None
_image_model = None


def get_models():
    """Lazy-initialise and return (general_model, image_model)."""
    global _general_model, _image_model
    if _general_model is None:
        try:
            _general_model = YOLO(os.path.join(model_path, general_model_name))
            _general_model.to(device)
            _image_model = YOLO(os.path.join(model_path, image_model_name))
            _image_model.to(device)
        except Exception as e:
            print(f"Error loading YOLO models: {e}")
            _general_model, _image_model = None, None
    return _general_model, _image_model


configs = {}
configs["paratext"] = {"sz": 640, "conf": 0.25, "rm": True, "classes": [0, 1]}
configs["imgtab"] = {"sz": 640, "conf": 0.35, "rm": True, "classes": [2, 3]}
configs["image"]   = {"sz": 640, "conf": 0.35, "rm": True, "classes": [0]}


def get_predictions(model, img2, config):
    res_dict = {"status": 1}
    try:
        for result in model.predict(
            source=img2,
            verbose=False,
            retina_masks=config["rm"],
            imgsz=config["sz"],
            conf=config["conf"],
            stream=True,
            classes=config["classes"],
            agnostic_nms=True,
        ):
            try:
                if result.masks and result.boxes:
                    res_dict["masks"] = result.masks.data
                    res_dict["boxes"] = result.boxes.data
                    res_dict["xyxy"] = result.boxes.xyxy.cpu()
                else:
                    res_dict["status"] = 0
                    return res_dict
                del result
                return res_dict
            except Exception:
                res_dict["status"] = 0
                return res_dict
    except Exception:
        res_dict["status"] = -1
        return res_dict


def get_masks(img):
    """
    Run YOLO segmentation on *img* and return bounding boxes for text regions.

    Models are fetched via get_models() so they are loaded lazily on first
    use.  Callers no longer pass model objects or configs — those are internal.
    """
    general_model, image_model = get_models()

    response = {"status": 1}
    if general_model is None or image_model is None:
        response["status"] = -1
        return response

    res = get_predictions(
        general_model,
        img,
        {"sz": 640, "conf": 0.25, "rm": True, "classes": [0, 1]},
    )

    if res["status"] == -1:
        response["status"] = -1
        return response

    try:
        response["boxes1"] = np.array(res["xyxy"], dtype=np.int32)
    except Exception as e:
        print(f"Error getting YOLO bounding boxes: {e}")
        response["status"] = 0
        response["boxes1"] = np.array([])
    return response


def custom_sort(arr, image_height=None):
    """
    Sort bounding boxes in reading order (top-to-bottom, then left-to-right).

    #5: The row-grouping tolerance scales with the image height (0.3 % of
    height, minimum 5 px) so that the same physical gap maps to the correct
    pixel distance regardless of scan resolution.  Pass ``image_height`` (in
    pixels) to enable scaling; omitting it falls back to the original fixed
    5 px tolerance for backward compatibility.
    """
    if arr.size == 0:
        return arr

    # #5: resolution-aware tolerance
    if image_height is not None:
        row_tolerance = max(5, int(image_height * 0.003))
    else:
        row_tolerance = 5  # original fixed fallback

    sorted_arr = arr[arr[:, 1].argsort()]

    i = 0
    while i < len(sorted_arr) - 1:
        j = i + 1
        while (
            j < len(sorted_arr)
            and sorted_arr[j, 1] - sorted_arr[i, 1] <= row_tolerance
        ):
            j += 1
        if j - i > 1:
            sorted_arr[i:j] = sorted_arr[i:j][sorted_arr[i:j, 0].argsort()]
        i = j
    return sorted_arr