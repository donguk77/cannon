import os
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

class LLMJudge:
    """
    [Track B 오프라인 최종 대법원 - Gemini 업데이트 버전]
    샴 네트워크(Siamese)마저도 "50~90% 확신도가 나와서 애매한데요?"라고 기권한 심하게 훼손된 이미지를 넘겨받아,
    사람의 눈처럼 '전체적인 맥락'을 종합적으로 추론하여 판결하는 최후의 VLM(비전 언어 모델) 판사입니다.
    기존 GPT/Claude에서 초고속 비전 인식이 가능한 Google Gemini API로 전격 교체되었습니다.
    """
    def __init__(self, api_key=None, model_name="gemini-1.5-flash"):
        # .env 파일의 GEMINI_API_KEY를 우선 사용, 없으면 인자로 받은 키 사용
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model_name = model_name
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"

    def encode_image_base64(self, image_path):
        """저용량 로컬 사진을 안전하게 API 서버로 전송하기 위해 텍스트 길이(Base64)로 쪼개어 압축합니다."""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def make_judgment(self, image_path, candidate_screen_name):
        """
        Gemini API 방아쇠를 찔러, "이 사진이 예비 정답 화면이 맞는지" 
        인간의 언어로 집요하게 캐묻고 단호한 결정을 받아냅니다.
        """
        if not self.api_key:
            return False, "API_KEY_MISSING", "시스템에 비전 LLM API 키가 없어 판독을 취소하고 작업자 확인 큐로 넘깁니다."
            
        base64_image = self.encode_image_base64(image_path)

        # 1. 제미나이 전용 구조화된 프롬프트 탑재
        system_prompt = (
            f"당신은 공장 기계 모니터 화면의 에러와 화면 번호를 정밀 검수하는 산업용 최고 등급 Vision AI입니다. "
            f"이 이미지가 '{candidate_screen_name}' 타겟 화면인지 분석하십시오. "
            f"조명 반사(글레어), 화질 노이즈, 글씨 뭉개짐이 심할 수 있습니다. 레이아웃과 남은 텍스트의 흔적을 조합하여 '예/아니오'로만 최종 판결하십시오."
        )

        headers = {
            "Content-Type": "application/json"
        }

        # Gemini REST API 규격에 맞춘 Payload 작성
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": system_prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": base64_image
                            }
                        }
                    ]
                }
            ],
            # 비용 절감 및 빠른 응답을 위한 토큰 제한
            "generationConfig": {
                "maxOutputTokens": 50
            }
        }

        # 2. 제미나이 대법관에게 심판 요청
        try:
            # 타임아웃 15초: 무한정 기다리다 프로그램 뻗는 사태를 방어
            response = requests.post(self.api_url, headers=headers, json=payload, timeout=15)
            response.raise_for_status()
            
            result_data = response.json()
            # Gemini 응답 파싱
            answer_text = result_data['candidates'][0]['content']['parts'][0]['text'].strip()
            
            # 3. 긍정의 단어(예, 맞다, True, Yes, 합격)가 나오면 최종 구제 처리하여 자동 라벨 부착
            is_passed = any(word in answer_text.lower() for word in ["yes", "예", "맞", "true", "합격", "확인"])
            
            status = "LLM_AUTO_LABELED" if is_passed else "LLM_REJECTED"
            return is_passed, status, answer_text

        except Exception as e:
            # API 터짐, 인터넷 끊김 등의 억까(상황) 발생 시 
            # 공장 프로그램 전체가 멈추면 대형 사고이므로 얌전히 "사람이 확인하세요" 폴더로 토스합니다.
            return False, "API_REQUEST_FAILED", str(e)
