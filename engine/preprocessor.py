import cv2
import numpy as np

class ImagePreprocessor:
    """
    공장 환경의 조명 변화(어두움, 역광 등)와 물리적 노이즈를 방어하기 위한 전처리 모듈입니다.
    YOLO(탐지)용 이미지와 ORB/OCR(분석)용 이미지를 다르게 처리하여 성능을 극대화합니다.
    """
    def __init__(self, clahe_clip_limit=2.0, clahe_tile_grid=(8, 8)):
        # 1. CLAHE (Contrast Limited Adaptive Histogram Equalization) 초기화
        # 너무 밝거나 어두운 영역의 대비를 '타일(Grid)' 단위로 조율하여 균일하게 맞춥니다.
        self.clahe = cv2.createCLAHE(clipLimit=clahe_clip_limit, tileGridSize=clahe_tile_grid)
        
        # 2. 샤프닝 커널 초기화 (윤곽선 강조용)
        # 글자나 UI 컴포넌트의 테두리를 날카롭게 만들어 ORB 특징점이 잘 잡히게 유도합니다.
        self.sharpen_kernel = np.array([
            [-1, -1, -1],
            [-1,  9, -1],
            [-1, -1, -1]
        ])

    def preprocess_for_yolo(self, frame):
        """
        YOLO 탐지용 전처리 (속도 우선)
        YOLO는 원본 컬러 환경에 강건하므로, 화질 저하가 없는 선에서 아주 가벼운 처리만 수행합니다.
        """
        # 현재는 원본을 그대로 통과시키지만, 추후 필요 시 노이즈 제거(Blur) 등을 추가할 수 있습니다.
        return frame.copy()

    def preprocess_for_orb(self, frame):
        """
        ORB/OCR 분석용 강력한 전처리 (정확도 우선)
        흑백 변환 -> 조명 평탄화(CLAHE) -> 윤곽 강조(Sharpening) 3단계를 거칩니다.
        """
        # 1. 흑백(Grayscale) 변환: 컬러 정보는 덜어내고 형태(명암)에만 집중합니다.
        if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame

        # 2. CLAHE 적용: 빛 반사나 그림자로 인해 어두워진 UI를 밝고 선명하게 복원합니다.
        clahe_applied = self.clahe.apply(gray)

        # 3. 샤프닝 적용: UI 버튼 테두리나 텍스트 윤곽을 극단적으로 강조합니다.
        sharpened = cv2.filter2D(clahe_applied, -1, self.sharpen_kernel)

        return sharpened

    @staticmethod
    def apply_masks(image, mask_list):
        """
        마스크 영역을 검정(0)으로 채워 ORB가 해당 구역을 무시하게 합니다.
        타겟 이미지와 실시간 프레임 양쪽에 동일하게 적용해야 매칭이 공정해집니다.
        mask_list: [{"x": 0~1, "y": 0~1, "w": 0~1, "h": 0~1}, ...]  (정규화 비율)
        """
        if not mask_list:
            return image
        result = image.copy()
        h, w = result.shape[:2]
        for m in mask_list:
            x1 = max(0, int(m["x"] * w))
            y1 = max(0, int(m["y"] * h))
            x2 = min(w, int((m["x"] + m["w"]) * w))
            y2 = min(h, int((m["y"] + m["h"]) * h))
            if x2 > x1 and y2 > y1:
                result[y1:y2, x1:x2] = 0
        return result
