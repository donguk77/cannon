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
        ORB/OCR 분석용 전처리 (수정됨: 노이즈 억제 우선)
        흑백 변환 -> 가우시안 블러(노이즈 제거) -> 조명 평탄화(CLAHE)
        """
        # 1. 흑백(Grayscale) 변환
        if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame

        # 2. 가우시안 블러 적용 (샤프닝의 반대! 모아레/빛반사를 뭉개버립니다)
        # (5, 5)는 뭉개는 강도입니다. 노이즈가 너무 심하면 (7, 7)로 올려도 좋습니다.
        

        def preprocess_for_orb(self, frame):
    
         if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
         else:
            gray = frame

        # 가우시안 대신 바이레터럴 필터 사용 (9: 필터 크기, 75: 색상/공간 뭉개기 강도)
        # 숫자를 (5, 50, 50) 등으로 조절하며 테스트할 수 있습니다.
        blurred = cv2.bilateralFilter(gray, 9, 75, 75)

        # 엣지가 살아있는 상태에서 대비를 높여 ORB 특징점을 극대화합니다.
        processed = self.clahe.apply(blurred)

        return processed
