import os
import cv2
import numpy as np
from PIL import Image
from ultralytics import YOLO

# Global variable to cache the model in memory so it doesn't reload every time
_yolo_model = None

def load_model(model_path="yolov8n.pt"):
    global _yolo_model
    if _yolo_model is None:
        try:
            # Check if custom mushroom model exists, otherwise fallback to the default YOLOv8 model
            if not os.path.exists(model_path):
                print(f"Custom model '{model_path}' not found. Downloading/using default yolov8n.pt...")
                _yolo_model = YOLO("yolov8n.pt") 
            else:
                _yolo_model = YOLO(model_path)
        except Exception as e:
            print(f"Error loading YOLO model: {e}")
            return None
    return _yolo_model

def detect_objects(image_pil, custom_model_path="mushroom_yolo.pt"):
    """
    Takes a PIL Image uploaded by Streamlit.
    Returns:
       1) the final PIL image with bounding boxes drawn securely.
       2) a list of detections (class names, confidences).
    """
    model = load_model(custom_model_path)
    if model is None:
        return image_pil, [{"error": "Model could not be loaded."}]
    
    # YOLO expects OpenCV format (BGR) or standard numpy arrays
    # Convert PIL Image (RGB) -> Numpy Array -> BGR
    image_cv = cv2.cvtColor(np.array(image_pil), cv2.COLOR_RGB2BGR)
    
    # Run Inference
    results = model(image_cv)
    
    # The results object contains the annotated image in BGR
    # .plot() returns a numpy array with bounding boxes automatically drawn
    annotated_img_bgr = results[0].plot()
    
    # Convert back to RGB for Streamlit/PIL rendering
    annotated_img_rgb = cv2.cvtColor(annotated_img_bgr, cv2.COLOR_BGR2RGB)
    final_pil = Image.fromarray(annotated_img_rgb)
    
    # Extract prediction data (Names and confidences)
    detections = []
    names = model.names
    for box in results[0].boxes:
        # box.cls is the class index, box.conf is the confidence float
        cls_idx = int(box.cls[0])
        conf = float(box.conf[0])
        class_name = names[cls_idx]
        detections.append({"label": class_name, "confidence": conf})
        
    return final_pil, detections
