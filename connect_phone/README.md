# Canon Monitor — 핸드폰 연결 앱

핸드폰 카메라로 모니터를 찍으면 PC의 기존 파이프라인(YOLO+ORB)이 실시간으로 분석하고
결과를 핸드폰 화면에 오버레이로 돌려줍니다.

---

## 구조

```
connect_phone/
├── server/          ← PC에서 실행하는 FastAPI WebSocket 서버
│   ├── app.py
│   ├── requirements.txt
│   └── start.bat    ← 더블클릭으로 서버 + 터널 시작
└── mobile/          ← React Native (Expo) 안드로이드 앱
    ├── App.tsx
    ├── package.json
    └── src/
        ├── screens/ (CameraScreen, SettingsScreen)
        ├── hooks/   (useWebSocket)
        └── components/ (OverlayView)
```

---

## PC 세팅 (1회)

### 1. 서버 의존성 설치
```bash
cd connect_phone/server
pip install -r requirements.txt
```

### 2. Cloudflare Tunnel 설치 (외부 접속용)
1. https://github.com/cloudflare/cloudflared/releases/latest 에서 `cloudflared-windows-amd64.exe` 다운로드
2. `cloudflared.exe` 파일을 `C:\Windows\System32\` 또는 PATH 경로에 복사

---

## PC 실행 방법

`connect_phone/server/start.bat` 더블클릭

→ 터미널에 아래처럼 출력됨:
```
2024-xx-xx INF  |  https://random-words.trycloudflare.com
```
이 URL을 복사해서 앱 설정에 붙여넣으세요.

---

## 앱 빌드 및 설치

### 개발 환경 준비
```bash
npm install -g expo-cli eas-cli
cd connect_phone/mobile
npm install
```

### APK 빌드 (EAS 사용)
```bash
# EAS 계정 로그인 (1회)
eas login

# APK 빌드 (preview = 직접 설치용)
eas build --platform android --profile preview
```
→ 빌드 완료 후 다운로드 링크로 APK 받아서 핸드폰에 직접 설치

### 개발 테스트 (빠른 확인용)
```bash
# Expo Go 앱 설치 후
npx expo start
# QR 코드 스캔
```

---

## 앱 사용 방법

1. 앱 최초 실행 → 설정 화면 자동 표시
2. PC 터미널의 `trycloudflare.com` URL 입력 → [저장 후 연결]
3. 이후 앱 실행 시 저장된 URL로 **자동 연결**
4. URL이 바뀌면 ⚙️ 버튼으로 설정 재진입

---

## 결과 오버레이 설명

| 표시 | 의미 |
|---|---|
| 🟢 점 + "연결됨" | WebSocket 연결 정상 |
| 🟡 점 + "연결 중..." | 재연결 시도 중 |
| 🔴 점 + "연결 끊김" | 서버 미실행 또는 네트워크 문제 |
| 초록 배지 "✓ PASS  Target N" | 해당 타겟 화면 인식 성공 |
| 빨간 배지 "✗ FAIL" | 인식 실패 |
| 초록 폴리곤 | YOLO가 감지한 모니터 경계 |

---

## 다중 기기

여러 핸드폰이 동시에 같은 URL에 접속 가능합니다.
각 기기가 독립적인 처리 큐를 가지므로 서로 영향 없음.
PC 성능에 따라 2~3대 동시 연결 권장 (각 3fps 기준).
