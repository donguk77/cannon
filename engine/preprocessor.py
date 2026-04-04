def preprocess_for_orb(self, frame):
    """
    ORB/OCR 분석용 강력한 전처리 (정확도 우선)
    흑백 변환 -> 밝기/대비 보정 -> 약한 블러 -> CLAHE -> 샤프닝
    """
    # 1. 흑백 변환
    if len(frame.shape) == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        gray = frame.copy()

    # 2. 밝기/대비 보정
    # alpha: 대비, beta: 밝기
    adjusted = cv2.convertScaleAbs(gray, alpha=1.1, beta=10)

    # 3. 약한 블러 (노이즈 제거)
    blurred = cv2.GaussianBlur(adjusted, (3, 3), 0)

    # 4. CLAHE 적용
    clahe_applied = self.clahe.apply(blurred)

    # 5. 샤프닝 적용
    sharpened = cv2.filter2D(clahe_applied, -1, self.sharpen_kernel)

    return sharpened

