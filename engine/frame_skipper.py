class FrameSkipper:
    """
    무거운 AI 연산(YOLO, ORB)으로 인해 프레임(FPS)이 떨어져 로봇 제어가 버벅이는 것을 막는 
    최적화의 핵, '프레임 스킵'과 '좀비 스태빌라이저' 모듈입니다.
    기능: 1프레임을 풀파워로 계산(65ms)하고 나면, 
    뒤따라오는 2장의 프레임은 0ms로 쿨하게 계산을 생략하고 넘겨버립니다.
    """
    def __init__(self, skip_frames=2):
        # 1. 쿨타임(건너뛸 장수)
        # 아키텍처 상 '10프레임 풀 계산 / 20프레임 휴식' 이므로, 기본적으로 2프레임을 건너뜁니다.
        self.skip_frames = skip_frames
        
        # 2. 이번 프레임을 쉴지 말지 판단하는 턴(Turn) 카운터
        self.frame_count = 0
        
        # 3. 좀비 스태빌라이저 (소프트웨어 잔상 기억 장치)
        # 건너뛰는 프레임(약 66ms) 동안 AI가 눈을 감고 있으므로,
        # 가장 최근에 찾았던 올바른 '모니터 박스'와 '1번 화면 결괏값'을 유령처럼 잠시 들고 있습니다.
        self.last_zombie_result = None

    def should_process(self):
        """
        이번 프레임은 "뇌를 켜서 직접 무거운 YOLO 처리를 할 프레임인가?"를 물어봅니다.
        반환값: True(일해라) / False(수면 모드, 예전 기억을 꺼내 써라)
        """
        self.frame_count += 1
        
        # 쉬어야 할 장수를 다 초과했으면 카운터를 체우고 새롭게 일하러 나갑니다.
        if self.frame_count > self.skip_frames:
            self.frame_count = 0
            return True
            
        # 아직 쿨타임(쉬는 중)이라면 일하지 않음을(False) 리턴
        return False

    def update_zombie_memory(self, detection_result):
        """
        방금 막 무거운 딥 스캐닝(Process 1)을 끝내서 얻어낸 가장 신선하고 정확한 
        (모니터 박스 좌표, 타겟 점수 70점 등) 결괏값을 '좀비 메모리'에 갱신(덮어쓰기)합니다.
        """
        self.last_zombie_result = detection_result

    def get_zombie_result(self):
        """
        계산을 쉬고 있는 수면 프레임(Skip 2) 동안 로봇과 작업자에게 내보낼 거짓말(잔상) 데이터를 반환합니다.
        이 덕분에 작업자 모니터에서는 깜빡임 없이 초록색 박스가 부드럽게 유지되며, 
        로봇은 에러 없이 안정적으로 1번 화면이 유지된다고 착각하고 부드럽게 동작합니다.
        """
        return self.last_zombie_result
