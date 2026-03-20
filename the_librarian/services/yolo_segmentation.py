import os
import numpy as np
from ultralytics import YOLO
from PIL import Image



# Use device from main file
device = "cpu"

model_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "model")
general_model_name = "e50_aug.pt"
image_model_name = "e100_img.pt"

try:
    general_model = YOLO(os.path.join(model_path, general_model_name))
    general_model.to(device)
    image_model = YOLO(os.path.join(model_path, image_model_name))
    image_model.to(device)
except Exception as e:
    print(f"Error loading YOLO models: {e}")
    general_model, image_model = None, None

configs = {}
configs["paratext"] = {"sz": 640, "conf": 0.25, "rm": True, "classes": [0, 1]}
configs["imgtab"] = {"sz": 640, "conf": 0.35, "rm": True, "classes": [2, 3]}
configs["image"] = {"sz": 640, "conf": 0.35, "rm": True, "classes": [0]}

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
            except Exception as e:
                res_dict["status"] = 0
                return res_dict
    except:
        res_dict["status"] = -1
        return res_dict

def get_masks(img, general_model, image_model, configs):
    response = {"status": 1}
    if general_model is None or image_model is None:
        response["status"] = -1
        return response
    
    res = get_predictions(
        general_model, img, {"sz": 640, "conf": 0.25, "rm": True, "classes": [0, 1]}
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

def custom_sort(arr):
    if arr.size == 0:
        return arr
    sorted_arr = arr[arr[:, 1].argsort()]

    i = 0
    while i < len(sorted_arr) - 1:
        j = i + 1
        while j < len(sorted_arr) and sorted_arr[j, 1] - sorted_arr[i, 1] <= 5:
            j += 1
        if j - i > 1:
            sorted_arr[i:j] = sorted_arr[i:j][sorted_arr[i:j, 0].argsort()]
        i = j
    return sorted_arr