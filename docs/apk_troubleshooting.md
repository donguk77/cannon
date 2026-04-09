# APK 빌드 & 카메라 문제 참조 문서

> 이 문서는 Claude가 미래 세션에서 같은 실수를 반복하지 않도록 쓴 참조 노트다.
> 문제가 생기면 여기를 먼저 보고 판단한다.

---

## 현재 프로젝트 구성 (고정값)

| 항목 | 값 | 이유 |
|------|-----|------|
| `react-native-vision-camera` | `4.7.3` (exact, `^` 없음) | 4.6.4는 RN 0.81.5와 API 비호환 |
| `react-native` | `0.81.5` | Expo SDK 54 번들 |
| `expo` | `~54.0.0` | — |
| `typescript` | `5.9.2` (exact) | Expo SDK 54 요구사항 `~5.9.2` |
| Gradle | `8.13` (withGradleWrapper.js로 강제) | VisionCamera 4.7.3 → AGP 8.10 → Gradle 8.13 필수 |
| EAS 빌드 이미지 | `ubuntu-24.04-jdk-17-ndk-r27b` | — |
| Android minSdkVersion | `26` | VisionCamera 요구사항 |

**패치 파일 목록** (npm install 시 자동 적용):
- `patches/expo-modules-core+3.0.29.patch` — Gradle 8.13 Kotlin DSL API 호환
- `patches/expo-modules-autolinking+3.0.24.patch` — Maven Central 429 대응 미러 추가

**Config plugins** (`app.json`에 등록):
- `./plugins/withGradleWrapper` — Gradle 버전 강제 8.13
- `./plugins/withMavenRepositories` — 메인 `settings.gradle`에 repo1 미러 추가
- `./plugins/withGradleInit` — `maven-mirrors.init.gradle` 생성 + gradlew에 `--init-script` 주입

---

## EAS 빌드 실패 사례

### [해결 완료] expo-build-properties 없음
**에러**: `React Native Gradle Plugin이 VisionCamera NDK 설정을 찾지 못함`
**원인**: `expo-build-properties` 플러그인 미설치
**해결**: `app.json` plugins에 추가, `minSdkVersion: 26`, `usesCleartextTraffic: true` 설정
```json
["expo-build-properties", { "android": { "minSdkVersion": 26, "usesCleartextTraffic": true } }]
```

---

### [해결 완료] expo-modules-core Kotlin 컴파일 오류
**에러**: `Unresolved reference 'extensions'` (ExpoModulesGradlePlugin.kt 등 3개 파일)
**원인**: `org.gradle.internal.extensions.core.extra`는 Gradle 내부 API → Gradle 8.8+에서 접근 불가
**해결**: `patch-package`로 import를 `org.gradle.kotlin.dsl.extra`로 교체
**파일**: `patches/expo-modules-core+3.0.29.patch`

---

### [해결 완료] Gradle 버전 불일치
**에러**: `Minimum supported Gradle version is 8.13. Current version is 8.8`
**원인**: VisionCamera 4.7.3 → AGP 8.10 → Gradle 8.13 필요. EAS가 기본으로 낮은 버전 사용
**해결**: `plugins/withGradleWrapper.js` — prebuild 후 `gradle-wrapper.properties`를 8.13으로 교체
**주의**: VisionCamera 버전을 올리면 Gradle 버전 요구사항도 따라 올라갈 수 있음

---

### [해결 완료] packageName을 찾지 못함
**에러**: `RNGP - Autolinking: Could not find project.android.packageName`
**원인**: 로컬 `android/` 폴더가 git에 포함되어 EAS prebuild가 불완전한 폴더 재사용
**해결**: `react-native.config.js`에 packageName 명시. 로컬 `android/` 폴더 git에서 제거
```js
module.exports = { project: { android: { packageName: 'com.canonmonitor.app' } } }
```
**교훈**: `android/` 폴더는 절대 git에 커밋하면 안 된다 (EAS가 prebuild로 생성함)

---

### [해결 완료] VisionCamera 4.6.4 + RN 0.81.5 API 비호환
**에러**:
- `CameraViewManager.kt:31 Return type mismatch: Map vs MutableMap`
- `Unresolved reference 'currentActivity'`
**원인**: VisionCamera 4.6.4가 RN 0.78 이전 API 사용. RN 0.78+에서 `currentActivity`가 `reactApplicationContext.currentActivity`로 변경됨
**해결**: VisionCamera를 `4.7.3`으로 업그레이드 (exact pin `"4.7.3"`, `^` 없음)
**교훈**: VisionCamera 버전은 `^` 없이 정확히 고정해야 한다. npm이 자동으로 올리면 RN API 비호환 발생

---

### [해결 완료] Maven Central HTTP 429 (Too Many Requests)
**에러**:
```
Could not GET 'https://repo.maven.apache.org/maven2/org/jetbrains/kotlin/kotlin-native-utils/1.9.24/...'
Received status code 429 from server: Too Many Requests
```
**원인**: EAS 빌드 서버 IP가 Maven Central에 rate limit됨. 이 문제는 반복해서 발생한다.

#### 왜 해결이 어려웠나 (삽질 기록)

**1차 시도 — 실패**: `withMavenRepositories.js`로 메인 `android/settings.gradle` 수정
- 실패 이유: 문제는 메인 settings.gradle이 아니라 `includeBuild`로 참조되는 내부 빌드에 있었음

**2차 시도 — 실패**: `patch-package`로 `expo-gradle-plugin/settings.gradle.kts`에 `cache-redirector.jetbrains.com/maven-central` 추가
- 실패 이유: `cache-redirector.jetbrains.com`은 **JetBrains 내부 CDN**이라 외부에서 접근 불가 → 404 반환 → Gradle이 조용히 통과 → 에러 로그에 이 URL이 아예 나타나지 않음

**3차 시도 — 성공**: 아래 3가지 동시 적용

```
[패치] expo-modules-autolinking+3.0.24.patch
  - expo-gradle-plugin/settings.gradle.kts: pluginManagement.repositories에 repo1.maven.org 추가
  - expo-autolinking-plugin-shared/build.gradle.kts: repositories에 repo1.maven.org 추가

[플러그인] withGradleInit.js
  - android/maven-mirrors.init.gradle 생성
  - gradlew에 --init-script 플래그 주입
  - gradle.beforeSettings 훅으로 모든 includeBuild에도 미러 적용
```

**핵심 구조 이해**:
```
메인 android/settings.gradle
    └── includeBuild(expo-gradle-plugin)  ← 여기가 문제
            └── expo-gradle-plugin/settings.gradle.kts  ← 독립 settings
                    └── expo-autolinking-plugin-shared/build.gradle.kts
```
`includeBuild`로 참조된 내부 빌드는 **자신의 settings 파일을 독립적으로** 사용한다.
메인 settings.gradle을 아무리 고쳐도 이 내부 빌드에는 영향이 없다.

**429 에러가 다시 생기면**:
1. `patches/expo-modules-autolinking+3.0.24.patch`가 올바른 URL(`repo1.maven.org/maven2/`)을 가리키는지 확인
2. `plugins/withGradleInit.js`가 `app.json`에 등록되어 있는지 확인
3. 그래도 안 되면 EAS 빌드를 재시도 (진짜 일시적 429일 수 있음)

**올바른 Maven 미러 URL**:
- ✅ `https://repo1.maven.org/maven2/` — Sonatype이 운영하는 원본 Maven Central (다른 서버)
- ❌ `https://repo.maven.apache.org/maven2/` — Apache 미러 (rate limit 걸리는 서버)
- ❌ `https://cache-redirector.jetbrains.com/maven-central` — JetBrains 내부 CDN, 외부 접근 불가

---

### [주의] expo doctor 경고가 빌드를 막는 경우
**증상**: `Run expo doctor` 단계에서 실패 후 빌드 중단
**원인**: 패키지 버전이 Expo SDK 요구사항과 다를 때
**확인법**: expo doctor가 권장하는 버전을 `package.json`에 맞추고 `npm install` 재실행
**현재 상태**: TypeScript `5.9.2`로 맞춤 (Expo SDK 54 요구 `~5.9.2`)

---

## 카메라 & 디스플레이 문제 사례

### [해결 완료] 카메라가 처음부터 찾을 수 없음 (APK 설치 후)
**증상**: APK 설치 후 앱 시작하면 "카메라 초기화 중..." 스피너가 영원히 돌거나 카메라를 찾지 못함
**원인**: `useCameraDevice('back')` 훅은 마운트 시점에 `Camera.getAvailableCameraDevices()`를 동기 호출한다.
이때 카메라 권한이 없으면 빈 배열 → `device = undefined`.
권한 승인 후 `CameraDevicesChanged` 이벤트가 일부 Android 기기에서 발화되지 않아 영구적으로 `undefined` 상태.

**해결**: `CameraScreen`을 권한 가드 컴포넌트와 실제 UI 컴포넌트로 분리
```tsx
// ✅ 올바른 구조
export default function CameraScreen({ serverUrl, onOpenSettings }) {
  const { hasPermission, requestPermission } = useCameraPermission();
  // 권한 없으면 권한 요청 화면 표시
  if (!hasPermission) return <PermissionScreen />;
  // 권한 확인 후에만 CameraContent 마운트 → useCameraDevice가 권한 있는 상태에서 초기화
  return <CameraContent serverUrl={serverUrl} onOpenSettings={onOpenSettings} />;
}

function CameraContent({ serverUrl, onOpenSettings }) {
  const device = useCameraDevice('back'); // 항상 권한 있는 상태에서 호출됨
  // ...
}
```

**추가**: device가 8초 이상 `undefined`면 "카메라를 찾을 수 없습니다 + 다시 시도" 버튼 표시 (무한 스피너 방지)

---

### [해결 완료] 카메라 깜빡임 (구버전 아키텍처 문제)
**증상**: 프레임이 교체될 때마다 화면이 깜빡임
**원인 (구버전)**: 카메라를 `opacity:0`으로 숨기고, `takeSnapshot()` 후 서버가 반환한 어노테이션 JPEG를 `<Image>`로 표시. Image source 교체 시마다 리렌더 → 깜빡임
**해결**: 카메라를 항상 표시, 서버는 JSON만 반환, SVG 오버레이를 별도 `OverlayView`로 표시

**현재 아키텍처 (올바른 구조)**:
```
[Camera 컴포넌트] → 항상 표시 (라이브 영상, 30fps)
[OverlayView]    → 서버 JSON 결과로 폴리곤/배지 표시 (zombie stabilize)
서버             → JSON만 반환 (frame 필드 없음)
```

---

### [해결 완료] 슬라이드쇼처럼 보임 + 오버레이가 전혀 표시 안 됨
**증상**: 라이브 영상이 영상처럼 보이지 않고 사진 나열처럼 끊김. PASS/FAIL 배지, 폴리곤 표시 안 됨.
**원인**: `showResult = isConnected && !!result?.frame` — 서버가 `frame` 필드를 보내지 않으므로 항상 `false`. `OverlayView`에 항상 `result=null`이 전달되어 오버레이가 완전히 비활성화됨.
```tsx
// ❌ 잘못된 코드 (서버가 frame을 안 보내므로 항상 false)
const showResult = isConnected && !!result?.frame;
<OverlayView result={showResult ? result : null} />

// ✅ 올바른 코드 (zombie stabilize)
const overlayResult = isConnected ? result : null;
<OverlayView result={overlayResult} />
```

**해결 후 동작 흐름 (zombie stabilize)**:
```
t=0ms    : 프레임 전송 → 라이브 카메라 표시 (30fps 부드러운 영상)
t=100ms  : 결과 수신  → overlayResult 업데이트 (폴리곤/배지 표시)
t=100~200ms: 라이브 카메라(계속 부드럽게) + 이전 결과 오버레이 유지 (zombie)
t=200ms  : 다음 프레임 전송 → overlayResult 그대로 (깜빡임 없음)
t=300ms  : 새 결과 수신 → overlayResult 업데이트
```

**교훈**: 서버 응답 구조가 바뀌면 클라이언트의 `showResult` 조건도 같이 확인해야 한다.
현재 서버(`connect_phone/server/app.py`)는 **frame 필드를 보내지 않는다**. 보내는 필드:
`status, target_id, score, roi_passed, roi_total, corners, processing_ms, yolo_hit`

---

## 수동으로 체크해야 할 패턴

### 빌드 전 체크리스트
- [ ] `android/` 폴더가 git에 없는지 확인 (`.gitignore`에 포함되어야 함)
- [ ] `patches/` 디렉토리에 최신 패치 파일이 있는지 확인
- [ ] `app.json` plugins에 `withGradleWrapper`, `withMavenRepositories`, `withGradleInit` 등록됐는지 확인
- [ ] `package.json`의 `postinstall`이 `patch-package`인지 확인

### VisionCamera 업그레이드 시 주의사항
VisionCamera 버전을 올리면 연쇄적으로 확인해야 할 것들:
1. 새 버전이 요구하는 Gradle/AGP 버전 확인 → `withGradleWrapper.js` 업데이트
2. RN API 호환성 확인 (특히 `currentActivity` 같은 deprecated API)
3. expo-modules-core 패치가 여전히 유효한지 확인

### 서버-클라이언트 JSON 필드 변경 시
서버가 반환하는 JSON 필드가 바뀌면:
1. `useWebSocket.ts`의 `MatchResult` 타입 업데이트
2. `CameraScreen.tsx`의 `showResult` / `overlayResult` 조건 재확인
3. `OverlayView.tsx`가 올바른 필드를 읽는지 확인

---

## 빌드 환경 참조

```
GitHub Actions: .github/workflows/eas-build.yml
  - connect_phone/mobile/** 변경 + main 브랜치 push → 자동 트리거
  - eas build --platform android --profile preview --non-interactive
  - EXPO_TOKEN: GitHub Secrets에 저장

EAS 프로젝트: expo.dev/accounts/donguknan/projects/canon-monitor-app
APK 다운로드: EAS 빌드 완료 후 expo.dev 또는 GUI 탭(QR 코드)에서 확인
```
