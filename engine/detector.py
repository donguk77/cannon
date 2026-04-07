import cv2
import numpy as np
import os
import json

class BezelDetector:
    """
    YOLOv8 모델로 모니터를 탐지하고, 원근 보정(warpPerspective)한 정면 이미지를 반환합니다.

    원근 보정 우선순위:
      ① YOLOv8-seg 모델 → 세그멘테이션 마스크 폴리곤으로 4코너 추출
      ② detection 전용 모델 → bbox 크롭 내 Canny+윤곽선으로 4코너 자동 검출 (Method B)
      ③ 모두 실패 → bbox 직사각형 크롭 (기존 방식)

    self.last_corners: 마지막 감지된 4코너 좌표 (원본 프레임 기준, float32 (4,2)).
                       화면에 사다리꼴 오버레이를 그릴 때 사용합니다.
                       탐지 실패 또는 직사각형 폴백 시에는 None.

    ⚠️  핵심 설계: from ultralytics import YOLO 를 모듈 최상단이 아닌
       __init__ 내부에서 Lazy-load 합니다.
       Qt GUI 프로세스 시작 시점에 torch DLL이 로드되면 Qt DLL과 충돌하여
       WinError 1114가 발생하므로, 최초 객체 생성 시점으로 임포트를 미룹니다.
    """

    def __init__(self, model_path="../yolov8n.pt", conf_threshold=0.5):
        from ultralytics import YOLO
        # NOTE: ONNX는 seg 모델의 경우 CPU에서 오히려 느림 (벤치: PT 52ms vs ONNX 70ms)
        # GPU 환경이면 아래 주석을 해제하여 ONNX 모드를 사용할 수 있음
        # onnx_path = model_path.replace(".pt", ".onnx")
        # if os.path.exists(onnx_path):
        #     print(f"[detector] ONNX 모드: {os.path.basename(onnx_path)}")
        #     self.model = YOLO(onnx_path, task="segment")
        # else:
        self.model = YOLO(model_path)
        self.conf_threshold = conf_threshold
        self.last_corners   = None   # (4, 2) float32 | None

    def detect_and_crop(self, frame, out_w=640, out_h=360):
        """
        모니터 탐지 후 원근 보정된 이미지 반환.

        반환값: (결과_이미지, (x1, y1, x2, y2))
                탐지 실패 시 (None, None)

        성공 시 self.last_corners 에 4코너 좌표 저장 (오버레이 표시용).
        """
        self.last_corners = None
        
        # UI에서 변경 가능한 해상도 설정 로드 (기본값 640)
        target_imgsz = 640
        try:
            cfg_path = os.path.join(os.path.dirname(__file__), "..", "data", "params_config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    target_imgsz = int(cfg.get("yolo_imgsz", 640))
        except:
            pass

        results = self.model(frame, verbose=False, imgsz=target_imgsz)

        if not (len(results) > 0 and len(results[0].boxes) > 0):
            return None, None

        boxes      = results[0].boxes
        best_box   = boxes[0]
        confidence = best_box.conf.item()

        if confidence < self.conf_threshold:
            return None, None

        x1, y1, x2, y2 = map(int, best_box.xyxy[0].tolist())
        fh, fw = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(fw, x2), min(fh, y2)
        bbox = (x1, y1, x2, y2)

        dst = np.array([
            [0,         0        ],
            [out_w - 1, 0        ],
            [out_w - 1, out_h - 1],
            [0,         out_h - 1],
        ], dtype=np.float32)

        # ── ① 세그멘테이션 마스크 → 원근 보정 ──────────────────────────────
        if results[0].masks is not None:
            try:
                mask_xy = results[0].masks.xy[0]
                corners = self._extract_quad(mask_xy)
                if corners is not None:
                    M      = cv2.getPerspectiveTransform(corners, dst)
                    warped = cv2.warpPerspective(frame, M, (out_w, out_h))
                    self.last_corners = corners
                    return warped, bbox
            except Exception as e:
                print(f"[detector] seg 원근 보정 실패: {e}")

        # ── ② OpenCV 윤곽선 → 원근 보정 (Method B) ───────────────────────────
        try:
            corners = self._detect_quad_from_bbox(frame, x1, y1, x2, y2)
            if corners is not None:
                M      = cv2.getPerspectiveTransform(corners, dst)
                warped = cv2.warpPerspective(frame, M, (out_w, out_h))
                self.last_corners = corners
                return warped, bbox
        except Exception as e:
            print(f"[detector] 윤곽선 원근 보정 실패: {e}")

        # ── ③ 폴백: bbox 직사각형 크롭 ──────────────────────────────────────
        cropped_img = frame[y1:y2, x1:x2]
        return cropped_img, bbox

    # ── 내부 유틸 ───────────────────────────────────────────────────────────────

    def _detect_quad_from_bbox(self, frame, x1, y1, x2, y2):
        """
        YOLO bbox 크롭 안에서 Canny 엣지 + 윤곽선으로 모니터 4코너를 검출합니다.
        성공 시 [좌상, 우상, 우하, 좌하] 원본 프레임 좌표 반환, 실패 시 None.

        알고리즘:
          1. 크롭 → 흑백 → GaussianBlur
          2. Canny (자동 임계값: 중앙값 기반 sigma=0.33)
          3. morphologyEx CLOSE (끊어진 엣지 연결)
          4. 가장 큰 윤곽선 추출
          5. approxPolyDP(eps 2~12%)로 4각형 단순화
          6. 4점이 안 나오면 Convex hull 극점 4개 폴백
        """
        crop_w = x2 - x1
        crop_h = y2 - y1
        if crop_w < 20 or crop_h < 20:
            return None

        crop     = frame[y1:y2, x1:x2]
        gray     = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        blurred  = cv2.GaussianBlur(gray, (5, 5), 0)

        # 자동 Canny 임계값 (sigma=0.33 방식)
        median = float(np.median(blurred))
        lo     = max(0,   int((1.0 - 0.33) * median))
        hi     = min(255, int((1.0 + 0.33) * median))
        edges  = cv2.Canny(blurred, lo, hi)

        # 모폴로지 닫기: 끊어진 엣지 이어붙이기
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        edges  = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        # 크롭 면적의 5% 이상인 윤곽선만 후보로
        min_area  = crop_w * crop_h * 0.05
        candidates = [c for c in contours if cv2.contourArea(c) >= min_area]
        if not candidates:
            return None

        largest   = max(candidates, key=cv2.contourArea)
        perimeter = cv2.arcLength(largest, True)

        # approxPolyDP: eps를 점점 늘려가며 4각형이 나올 때까지 시도
        for eps_ratio in [0.02, 0.04, 0.06, 0.08, 0.10, 0.12]:
            approx = cv2.approxPolyDP(largest, eps_ratio * perimeter, True)
            if len(approx) == 4:
                pts = approx.reshape(4, 2).astype(np.float32)
                pts[:, 0] += x1   # crop → 원본 프레임 좌표
                pts[:, 1] += y1
                return self._order_corners(pts)

        # 4점이 안 나오면 convex hull 극점 폴백
        hull = cv2.convexHull(largest)
        if hull is not None and len(hull) >= 4:
            pts = hull.reshape(-1, 2).astype(np.float32)
            pts[:, 0] += x1
            pts[:, 1] += y1
            return self._order_corners(pts)

        return None

    def _extract_quad(self, polygon):
        """
        세그멘테이션 폴리곤 → 4코너(좌상·우상·우하·좌하) 추출.

        1차: approxPolyDP 엡실론 2~10%로 딱 4점 추출.
        2차: Convex hull 극점 폴백.
        """
        if len(polygon) < 4:
            return None

        pts       = np.array(polygon, dtype=np.float32).reshape(-1, 1, 2)
        perimeter = cv2.arcLength(pts, True)

        for eps_ratio in [0.02, 0.04, 0.06, 0.08, 0.10]:
            approx = cv2.approxPolyDP(pts, eps_ratio * perimeter, True)
            if len(approx) == 4:
                return self._order_corners(approx.reshape(4, 2))

        hull = cv2.convexHull(pts)
        if hull is None or len(hull) < 4:
            return None
        return self._order_corners(hull.reshape(-1, 2))

    @staticmethod
    def _order_corners(pts):
        """
        임의의 N점을 [좌상, 우상, 우하, 좌하] 순서로 정렬합니다.
          - 좌상: x+y 최소
          - 우하: x+y 최대
          - 우상: x-y 최대 (x 크고 y 작음)
          - 좌하: x-y 최소 (x 작고 y 큼)
        """
        pts  = np.array(pts, dtype=np.float32)
        s    = pts.sum(axis=1)
        diff = pts[:, 0] - pts[:, 1]
        return np.array([
            pts[np.argmin(s)],       # 좌상
            pts[np.argmax(diff)],    # 우상
            pts[np.argmax(s)],       # 우하
            pts[np.argmin(diff)],    # 좌하
        ], dtype=np.float32)
