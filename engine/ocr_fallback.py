import cv2

# OCR(광학 문자 인식) 라이브러리인 pytesseract를 사용합니다.
# (이 모듈은 공장 PC에 설치된 Tesseract-OCR 엔진과 연동되어 작동합니다)
try:
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


class OCRFallback:
    """
    [제2의 방어막 / 듀얼 트랙 폴백 시스템]
    만약 공장 창문으로 햇빛이 너무 강하게 들어와 모니터 모양(ORB 특징점) 전체가 하얗게 타버려서 
    수학적 매칭(matcher.py)이 완전히 실패했을 때, 최후의 보루로 화면 안의 "글자(Text)"를
    읽어내어 지금 켜진 화면이 몇 번 화면인지 유추하는 비상용 구명조끼 모듈입니다.
    """
    def __init__(self, expected_keywords=None):
        # 1. 화면 식별을 위한 '핵심 키워드' (예: 1번 화면엔 무조건 'START' 버튼이 있다)
        if expected_keywords is None:
            # 기본값으로 가장 흔히 쓰이는 공장 기기 버튼명들을 등록
            self.expected_keywords = ['start', 'next', 'cancel', 'ok', 'error', 'menu']
        else:
            self.expected_keywords = [k.lower() for k in expected_keywords]
            
        # 2. Tesseract 속도 광폭 튜닝 (문서 명시 스펙: 15FPS 이상 방어)
        # --psm 11: 띄어쓰기 무시하고 글자가 있는 덩어리를 닥치는대로 빠르게 읽어라
        # 영문 대소문자와 숫자만 읽도록 화이트리스트를 걸어 한글/특수문자 연산 비용을 0으로 만듦
        self.config = r'--psm 11 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'

    def extract_text(self, cropped_bezel_img):
        """
        YOLO가 1차적으로 잘라준 모니터 화면(cropped_bezel_img) 안에서
        Tesseract 엔진을 돌려 글자만 텍스트(String)로 무식하게 뽑아옵니다.
        """
        if not OCR_AVAILABLE:
            return ""
            
        # 이미 앞선 preprocessor.py 에서 흑백 변환과 샤프닝(글씨 테두리 강조)이
        # 끝난 이미지가 들어오므로, 별도 전처리 없이 즉시 OCR을 돌려 속도를 아낍니다.
        text = pytesseract.image_to_string(cropped_bezel_img, lang='eng', config=self.config)
        
        # 가져온 글자에서 불필요한 공백을 날리고 전부 소문자로 통일하여 비교하기 쉽게 깎음
        return text.strip().lower()

    def rescue_judge(self, cropped_bezel_img):
        """
        ORB에서 특징점 점수를 0점 받아 억울하게 탈락한 프레임을 최종 재판하는 곳입니다.
        반환값: (최종 구제합격 여부 True/False, 추출된 텍스트 로그)
        """
        extracted_text = self.extract_text(cropped_bezel_img)
        
        # 글자를 아예 못 읽어왔다면 가차없이 판정 불가(False)
        if not extracted_text:
            return False, "NO_TEXT_FOUND"
            
        # 추출해 온 텍스트 뭉치 안에, 우리가 이 화면에 있을 거라고 예상한 '키워드(예: next)'가
        # 단 한 글자라도 섞여 있다면? "아, 모양은 일그러졌는데 글씨 보니까 1번 화면 맞네!" 하고 구제(True)
        for keyword in self.expected_keywords:
            if keyword in extracted_text:
                return True, extracted_text
                
        # 글자는 읽었으나 우리가 찾는 화면의 버튼 이름이 안 보이면 다른 화면이므로 기각(False)
        return False, extracted_text
