import cv2
import numpy as np
import os
import json

RESIZE_W, RESIZE_H = 640, 360   # 정규화 해상도 (16:9)
ROI_MATCH_THRESHOLD = 7         # ROI 크롭 단위 매칭 합격 컷오프 (10→7: 스크린샷-카메라 도메인 갭 보정)

class ScreenMatcher:
    """
    잘라낸 모니터 화면이 우리가 찾는 1번(혹은 2번) 타겟 화면과 동일한지 
    '제로샷(1장만으로 비교)' 방식으로 판별하는 핵심 ORB 특징점 매칭 모듈입니다.
    """
    def __init__(self, orb_nfeatures=700, lowe_ratio=0.75, match_threshold=25):
        # 1. ORB (특징점 추출기)
        # 딥러닝과 달리 규칙 기반으로 빠르고 가볍게 이미지의 특징점(코너 등) 700개를 찾습니다.
        # 문서 검증 지표인 65ms 속도 제한을 맞추기 위한 극단적 경량화 세팅입니다.
        self.orb = cv2.ORB_create(nfeatures=orb_nfeatures)

        # 2. 특징점 비교기 (Brute-Force Matcher)
        # ORB는 흑백 이진수(Binary)로 구성되므로 해밍 거리(NORM_HAMMING)를 사용하여 쌍을 찾습니다.
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

        # 3. 판별 정확도 세팅 (문서 상 'Recall 75% 방어' 달성을 위한 파라미터)
        self.lowe_ratio = lowe_ratio           # 노이즈를 거르는 깐깐함의 척도 (Lowe's Ratio Test)
        self.match_threshold = match_threshold # "몇 쌍 이상 똑같으면 같은 화면으로 칠래?" 라는 합격 컷오프

        # 4. 실시간 파이프라인용 전체 마스크 합집합 (load_targets_from_dir 호출 시 갱신)
        self.union_masks = []

    def get_features(self, image):
        """이미지에서 특징점(KeyPoints)과 그 특징을 설명하는 DNA(Descriptors)를 뽑아냅니다."""
        # 이전에 전처리기(Preprocessor)에서 넘어온 흑백 이미지가 들어옵니다.
        kp, des = self.orb.detectAndCompute(image, None)
        return kp, des

    def compare_screens(self, query_img, target_des):
        """(하위 호환용) 이미지 그대로 받아서 추출 후 매칭"""
        if query_img is None or target_des is None:
            return 0, False
        kp1, des1 = self.get_features(query_img)
        return self.compare_descriptors(des1, target_des)

    def compare_descriptors(self, query_des, target_des, threshold=None):
        """
        초고속 비교 전용: 이미 화면에서 뽑아둔 DNA(query_des)를 재입력받아서
        여러 타겟(1.png~4.png)과 중복 연산 없이 빠르게 매칭만 수행합니다.
        threshold: None이면 self.match_threshold(전체 이미지 기본값) 사용.
                   ROI 크롭용은 ROI_MATCH_THRESHOLD를 넘겨 사용.
        """
        if query_des is None or len(query_des) == 0 or target_des is None:
            return 0, False

        cutoff = threshold if threshold is not None else self.match_threshold

        # KNN 매칭
        try:
            matches = self.matcher.knnMatch(query_des, target_des, k=2)
        except Exception:
            return 0, False

        # Lowe's Ratio Test (리스트 컴프리헨션으로 속도 3~4배 최적화)
        try:
            # 대부분의 경우 match_pair가 2개(m, n)의 원소를 가짐
            good_matches = [m for m, n in matches if m.distance < self.lowe_ratio * n.distance]
        except ValueError:
            # 극히 드물게 2개가 아닌 경우가 섞여 있을 때를 위한 폴백
            good_matches = [pair[0] for pair in matches if len(pair) == 2 and pair[0].distance < self.lowe_ratio * pair[1].distance]

        score = len(good_matches)
        return score, (score >= cutoff)

    def load_targets_from_dir(self, target_dir, roi_config_path=None, detector=None, mask_config_path=None):
        """
        target_image 폴더에서 이미지를 읽어 ORB 특징점을 추출합니다.

        detector가 주어지면 타겟 이미지에도 YOLO를 적용해 베젤 영역을 자동 크롭합니다.
        이렇게 하면 라이브 피드와 동일한 좌표계가 되어 ROI 없이도 정확한 매칭이 가능합니다.

        roi_config_path가 주어지면 다중 ROI 방식을 사용합니다:
          - 각 이미지를 RESIZE_W×RESIZE_H로 정규화
          - roi_config에 정의된 ROI 영역(+5% 패딩)별로 크롭 후 특징점 추출
          - 반환값: { '1': {'rois': [(des, x1,y1,x2,y2), ...], 'full': des, 'n_rois': N}, ... }

        roi_config_path가 없거나 해당 파일에 ROI가 없으면 전체 이미지 방식(fallback):
          - 반환값: { '1': {'rois': [], 'full': des, 'n_rois': 0}, ... }
        """
        PAD = 0.05  # ROI 경계 5% 패딩 (수동 크롭 vs YOLO 크롭 오차 흡수)

        # ROI 설정 로드
        roi_config = {}
        if roi_config_path and os.path.isfile(roi_config_path):
            try:
                with open(roi_config_path, 'r', encoding='utf-8') as f:
                    roi_config = json.load(f)
                print(f"[matcher] ROI 설정 로드 완료: {len(roi_config)}개 타겟")
            except Exception as ex:
                print(f"[matcher] ROI 설정 로드 실패 (전체 이미지 방식으로 폴백): {ex}")

        # 마스크 설정 로드
        mask_config = {}
        if mask_config_path and os.path.isfile(mask_config_path):
            try:
                with open(mask_config_path, 'r', encoding='utf-8') as f:
                    mask_config = json.load(f)
                print(f"[matcher] 마스크 설정 로드 완료: {len(mask_config)}개 타겟")
            except Exception as ex:
                print(f"[matcher] 마스크 설정 로드 실패: {ex}")

        # 실시간 파이프라인용 합집합 마스크 초기화
        self.union_masks = []

        targets = {}
        if not os.path.isdir(target_dir):
            print(f"[matcher] 타겟 폴더를 찾을 수 없습니다: {target_dir}")
            return targets

        try:
            from engine.preprocessor import ImagePreprocessor
            preprocessor = ImagePreprocessor()
        except Exception:
            preprocessor = None

        for fname in sorted(os.listdir(target_dir)):
            if not fname.lower().endswith((".png", ".jpg", ".jpeg")):
                continue
            img_path = os.path.join(target_dir, fname)
            # ⚠️ 한글 경로 우회: cv2.imread → np.fromfile+imdecode
            try:
                buf = np.fromfile(img_path, dtype=np.uint8)
                img_color = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            except Exception as ex:
                print(f"[matcher] 이미지 로드 실패: {fname} ({ex})")
                continue
            if img_color is None:
                continue

            # ① YOLO 크롭 → 640×360 정규화
            #    detector가 있으면 타겟 이미지에도 YOLO를 적용해 라이브 피드와 동일한 좌표계로 맞춤
            if detector is not None:
                try:
                    cropped, _ = detector.detect_and_crop(img_color)
                    if cropped is not None and cropped.size > 0:
                        img_color = cv2.resize(cropped, (RESIZE_W, RESIZE_H))
                        print(f"[matcher] {fname}: YOLO 크롭 적용")
                    else:
                        img_color = cv2.resize(img_color, (RESIZE_W, RESIZE_H))
                        print(f"[matcher] {fname}: YOLO 미감지 → 전체 이미지 사용")
                except Exception as ex:
                    img_color = cv2.resize(img_color, (RESIZE_W, RESIZE_H))
                    print(f"[matcher] {fname}: YOLO 크롭 실패 ({ex}) → 전체 이미지 사용")
            else:
                img_color = cv2.resize(img_color, (RESIZE_W, RESIZE_H))

            # ② 전체 이미지 전처리 → fallback용 특징점
            if preprocessor:
                img_gray = preprocessor.preprocess_for_orb(img_color)
            else:
                img_gray = cv2.cvtColor(img_color, cv2.COLOR_BGR2GRAY)

            # 마스크 적용 (동적 영역 제거)
            # apply_masks는 @staticmethod이므로 인스턴스로 호출 가능
            target_masks = mask_config.get(fname, [])
            if target_masks and preprocessor is not None:
                img_gray = preprocessor.apply_masks(img_gray, target_masks)
                for m in target_masks:
                    if m not in self.union_masks:
                        self.union_masks.append(m)
                print(f"[matcher] {fname}: 마스크 {len(target_masks)}개 적용")
            elif target_masks:
                print(f"[matcher] {fname}: ImagePreprocessor 로드 실패 → 마스크 미적용")

            _, des_full = self.get_features(img_gray)

            screen_id = os.path.splitext(fname)[0]

            # ③ ROI별 특징점 추출
            roi_defs = roi_config.get(fname, [])
            roi_list = []

            for roi in roi_defs:
                rx, ry, rw, rh = roi['x'], roi['y'], roi['w'], roi['h']

                # 5% 패딩 적용
                rx_p = max(0.0, rx - PAD * rw)
                ry_p = max(0.0, ry - PAD * rh)
                rw_p = min(1.0 - rx_p, rw * (1 + 2 * PAD))
                rh_p = min(1.0 - ry_p, rh * (1 + 2 * PAD))

                # 비율 → 픽셀 변환
                x1 = int(rx_p * RESIZE_W)
                y1 = int(ry_p * RESIZE_H)
                x2 = min(int((rx_p + rw_p) * RESIZE_W), RESIZE_W)
                y2 = min(int((ry_p + rh_p) * RESIZE_H), RESIZE_H)

                if x2 - x1 < 10 or y2 - y1 < 10:
                    continue

                roi_crop = img_gray[y1:y2, x1:x2]
                _, des_roi = self.get_features(roi_crop)
                if des_roi is not None and len(des_roi) > 0:
                    roi_list.append((des_roi, x1, y1, x2, y2))

            targets[screen_id] = {
                'rois':   roi_list,
                'full':   des_full,
                'n_rois': len(roi_list),
            }

            if roi_list:
                print(f"[matcher] {fname}: ROI {len(roi_list)}개 로드 완료")
            else:
                full_cnt = len(des_full) if des_full is not None else 0
                print(f"[matcher] {fname}: 전체 이미지 방식 (특징점 {full_cnt}개)")

        return targets
