import cv2
import numpy as np
import os

class BezelDetector:
    """
    YOLOv8 모델을 활용하여 화면 내에서 기계의 '가장자리(베젤)'만
    정확히 탐지하고 해당 영역을 잘라내는(Crop) 1차 방어막 전담 모듈입니다.
    
    ⚠️ 핵심 설계: from ultralytics import YOLO 를 모듈 최상단이 아닌
       __init__ 내부에서 Lazy-load 합니다.
       Qt GUI 프로세스 시작 시점에 torch DLL이 로드되면 Qt DLL과 충돌하여
       WinError 1114가 발생하므로, 최초 객체 생성 시점으로 임포트를 미룹니다.
    """
    def __init__(self, model_path="../yolov8n.pt", conf_threshold=0.5):
        # Lazy-load: 이 순간에만 ultralytics/torch DLL을 메모리에 올림
        from ultralytics import YOLO
        self.model = YOLO(model_path)
        self.conf_threshold = conf_threshold

    def detect_and_crop(self, frame):
        """
        주어진 프레임 안에서 모니터 베젤을 탐지하고 해당 직사각형 테두리만 잘라 반환합니다.
        반환값:
            - cropped_img: 잘려진 베젤 화면 이미지 (탐지 실패 시 None)
            - bbox: 원본 프레임에서의 (x1, y1, x2, y2) 좌표
        """
        results = self.model(frame, verbose=False)

        if len(results) > 0 and len(results[0].boxes) > 0:
            boxes = results[0].boxes
            best_box = boxes[0]
            confidence = best_box.conf.item()

            if confidence >= self.conf_threshold:
                x1, y1, x2, y2 = map(int, best_box.xyxy[0].tolist())
                h, w = frame.shape[:2]
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                cropped_img = frame[y1:y2, x1:x2]
                return cropped_img, (x1, y1, x2, y2)

        return None, None
