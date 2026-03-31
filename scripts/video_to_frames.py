import cv2
import os

class VideoToFrameConverter:
    """
    [야간 자가 진전을 위한 첫 단추: 에러 영상 압축 분할기]
    사용자가 No-Code GUI 화면(Training Tab)에 거대한 에러 동영상 파일을 끌어다 놓으면,
    이 스크립트가 뒤에서 영상을 받아 1초당 N 장 분량의 사진(Frame) 단위로 쪼개어
    오프라인 샴(Siamese) 네트워크가 먹어치우기 좋은 형태로 `/data/pending/` 에 보관합니다.
    """
    def __init__(self, output_dir="../data/pending"):
        self.output_dir = output_dir
        # 출력 대상인 pending(애매함 대기소) 폴더가 삭제되어 없다면 즉석에서 뼈대를 세워줍니다.
        os.makedirs(self.output_dir, exist_ok=True)

    def extract_frames(self, video_path, capture_fps=5):
        """
        거대한 비디오 파일을 잘라내는 핵심 심장부.
        :param capture_fps: 이 값이 매우 중요. 1초에 30장 원본을 전부 꺼내면 '디스크 용량 한도 초과'로
                            로컬 컴퓨터가 뻗습니다. 1초에 단 5장만 빼내어 공장 여유 하드 디스크를 보호합니다.
        """
        if not os.path.exists(video_path):
            print(f"🚨 타겟 파일을 찾을 수 없습니다: {video_path}")
            return False

        # OpenCV를 통해 화면에 표출하지 않고 무음(백그라운드) 모드로 무겁지 않게 동영상 파일을 열어젖힘
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print("🚨 동영상 코덱 문제 혹은 파일 파손입니다.")
            return False

        # 영상 고유 원본의 초당 장수(FPS). (요즘 스마트폰이나 공장 카메라는 주로 30입니다)
        original_fps = cap.get(cv2.CAP_PROP_FPS)
        if original_fps == 0 or original_fps != original_fps:
            original_fps = 30.0
            
        # "1초에 30장 중에 5장만 뺄 거면, 몇 장마다 빼야 하지? 아 6장에 1개씩이구나!" (수학 계산식)
        frame_interval = int(original_fps / capture_fps) if capture_fps > 0 else 1

        print(f"🎥 영상 분할 시작 ... [ 타겟: {os.path.basename(video_path)} ]")
        
        frame_count = 0
        saved_count = 0

        # 영화 필름처럼 동영상을 죽 끌고 오며 찰칵찰칵 루프합니다.
        while True:
            ret, frame = cap.read()
            # 필름 끝에 봉착하면 정지
            if not ret:
                break
                
            # 지정된 타이밍 간격(예: 6번째) 사진만 저장하고, 나머지 5장은 휴지통으로 버려 속도/용량을 방어.
            if frame_count % frame_interval == 0:
                # 시스템 상 겹치지 않는 유니크한 시간/숫자 기반의 이름을 부여하여 파이프라인 교통사고 방지
                out_filename = f"error_case_{saved_count:04d}.jpg"
                out_filepath = os.path.join(self.output_dir, out_filename)
                
                # 가장 보편적인 JPG 퀄리티 포맷으로 썰어서 하드디스크에 저장
                cv2.imwrite(out_filepath, frame)
                saved_count += 1
                
            frame_count += 1

        cap.release()
        print(f"✅ 동영상 분할 처리 완수! 총 {saved_count} 개의 불량 의심 사진이 '{self.output_dir}' 통에 담김.")
        return True

if __name__ == "__main__":
    # 나중에 파이프라인에서 import 해서 쓰지만, 급할 땐 터미널로도 쏠 수 있게 뚫어둔 입구
    import sys
    converter = VideoToFrameConverter()
    if len(sys.argv) > 1:
        converter.extract_frames(sys.argv[1])
    else:
        print("💡 [사용법 안내] 뒤에 타겟 .mp4 경로를 붙여주세요 (GUI 화면의 드래그 앤 드롭을 쓴다면 무시하세요)")
