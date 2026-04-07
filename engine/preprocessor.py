import cv2
import numpy as np

class ImagePreprocessor:
    """
    공장 환경의 조명 변화(어두움, 역광 등)와 물리적 노이즈를 방어하기 위한 전처리 모듈입니다.
    YOLO(탐지)용 이미지와 ORB/OCR(분석)용 이미지를 다르게 처리하여 성능을 극대화합니다.

    ORB 전처리 파이프라인 (6단계):
      [1] Grayscale       — 필수, 컬러 정보 제거
      [2] Gaussian Blur   — 선택(blur_ksize > 0), 노이즈·모아레 제거
      [3] Gamma 보정      — 선택(gamma ≠ 1.0), 어두운 화면 선보정
      [4] CLAHE           — 조명 불균일 보정
      [5] Sharpening      — 선택(sharpen_amount > 0), 언샤프 마스킹으로 윤곽 강조

    순서 근거:
      - Blur가 CLAHE 앞에 오는 이유: 노이즈를 먼저 제거해야 CLAHE가 노이즈를 증폭시키지 않음
      - Gamma가 CLAHE 앞에 오는 이유: 화면이 어두울 때 Gamma로 먼저 대역을 넓혀야 CLAHE 효과 극대화
      - Sharpening이 마지막인 이유: 확정된 픽셀 범위에서 적용해야 클리핑 아티팩트 최소화
    """
    def __init__(self,
                 clahe_clip_limit=2.0,
                 clahe_tile_grid=(8, 8),
                 blur_ksize=0,
                 gamma=1.0,
                 sharpen_amount=1.0):
        """
        Parameters
        ----------
        clahe_clip_limit  : CLAHE 증폭 한계 배율 (0.5~8.0, 기본 2.0)
        clahe_tile_grid   : CLAHE 타일 격자 크기 (N×N, 기본 (8,8))
        blur_ksize        : 가우시안 블러 커널 크기 (0=꺼짐, 3/5/7)
                            0이 아닌 짝수 값은 자동으로 홀수+1로 올림
        gamma             : 감마 보정 지수 (1.0=꺼짐, <1.0=밝게, >1.0=어둡게)
        sharpen_amount    : 언샤프 마스킹 강도 (0.0=꺼짐, 1.0=중간, 2.0=강함)
                           이전의 고정 3×3 커널 방식을 대체.
                           기존 동작과 동등한 강도: ~1.5
        """
        # ── [4] CLAHE ──────────────────────────────────────────────────────────
        self.clahe = cv2.createCLAHE(clipLimit=clahe_clip_limit,
                                     tileGridSize=clahe_tile_grid)

        # ── [2] Blur ───────────────────────────────────────────────────────────
        # ksize는 홀수여야 함 (0=꺼짐, 짝수는 자동 올림)
        k = int(blur_ksize)
        if k <= 0:
            self.blur_ksize = 0
        elif k % 2 == 0:
            self.blur_ksize = k + 1
        else:
            self.blur_ksize = k

        # ── [3] Gamma LUT (미리 계산, 런타임 오버헤드 ~0) ─────────────────────
        self.gamma = float(gamma)
        if abs(self.gamma - 1.0) > 0.01:
            lut = np.power(np.arange(256) / 255.0, self.gamma) * 255.0
            self._gamma_lut = lut.astype(np.uint8)
        else:
            self._gamma_lut = None

        # ── [5] Sharpening ─────────────────────────────────────────────────────
        self.sharpen_amount = float(sharpen_amount)

    def preprocess_for_yolo(self, frame):
        """
        YOLO 탐지용 전처리 (속도 우선)
        YOLO는 원본 컬러 환경에 강건하므로, 화질 저하가 없는 선에서 아주 가벼운 처리만 수행합니다.
        """
        return frame.copy()

    def preprocess_for_orb(self, frame):
        """
        ORB/OCR 분석용 전처리 (정확도 우선)
        Grayscale → [Blur] → [Gamma] → CLAHE → [Sharpening] 순서로 적용합니다.
        [] 표시는 파라미터에 따라 선택적으로 적용됨을 의미합니다.
        """
        # ── [1] Grayscale ──────────────────────────────────────────────────────
        if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame.copy()

        # ── [2] Gaussian Blur (노이즈·모아레 제거) ─────────────────────────────
        # 모아레 패턴: 카메라로 모니터를 촬영할 때 픽셀 격자 간섭으로 발생하는 줄무늬.
        # ORB가 이것을 실제 특징점으로 오인하므로, CLAHE 전에 제거해야 증폭 방지.
        if self.blur_ksize > 0:
            gray = cv2.GaussianBlur(gray, (self.blur_ksize, self.blur_ksize), 0)

        # ── [3] Gamma Correction (어두운 화면 선보정) ─────────────────────────
        # 공장 조명 절약 모드 등으로 화면이 전체적으로 어두울 때,
        # CLAHE만으로는 협소한 밝기 구간 안에서 조정하므로 효과가 제한됨.
        # Gamma < 1.0으로 먼저 밝혀주면 CLAHE가 더 넓은 동적 범위에서 작동.
        if self._gamma_lut is not None:
            gray = cv2.LUT(gray, self._gamma_lut)

        # ── [4] CLAHE (조명 불균일 보정) ──────────────────────────────────────
        enhanced = self.clahe.apply(gray)

        # ── [5] Sharpening — Unsharp Masking ──────────────────────────────────
        # 언샤프 마스킹: 원본에서 가우시안 블러 버전을 빼서 엣지를 강조.
        # amount=1.0: result = 2*orig - blur5x5 (중간 강도)
        # amount=2.0: result = 3*orig - 2*blur5x5 (강한 강도)
        # 이전의 고정 3×3 Laplacian 커널보다 amount 조절로 세밀한 제어 가능.
        if self.sharpen_amount > 0.0:
            blur5 = cv2.GaussianBlur(enhanced, (5, 5), 1.0)
            sharpened = cv2.addWeighted(
                enhanced, 1.0 + self.sharpen_amount,
                blur5,    -self.sharpen_amount,
                0
            )
        else:
            sharpened = enhanced

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
