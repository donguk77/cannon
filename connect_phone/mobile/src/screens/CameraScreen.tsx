import React, { useRef, useState, useEffect, useCallback } from 'react';
import {
  View, TouchableOpacity, Text, StyleSheet,
  Dimensions, SafeAreaView, ActivityIndicator, Image,
} from 'react-native';
import {
  Camera,
  useCameraDevice,
  useCameraPermission,
  useCameraFormat,
} from 'react-native-vision-camera';
import * as ImagePicker from 'expo-image-picker';
import OverlayView from '../components/OverlayView';
import { useWebSocket } from '../hooks/useWebSocket';

const { width: SCREEN_W } = Dimensions.get('window');

const SEND_INTERVAL_MS = 200;   // 실시간 모드 전송 간격 (5fps)
const JPEG_QUALITY     = 50;
const ZOOM_STEP        = 0.5;

type Mode = 'photo' | 'file' | 'live';

type Props = {
  serverUrl: string;
  onOpenSettings: () => void;
};

// ── 모드 메타 ──────────────────────────────────────────────────────────────
const MODE_META: Record<Mode, { label: string; icon: string }> = {
  photo: { label: '사진', icon: '📸' },
  file:  { label: '파일', icon: '📁' },
  live:  { label: '실시간', icon: '🎥' },
};

export default function CameraScreen({ serverUrl, onOpenSettings }: Props) {
  const { hasPermission, requestPermission } = useCameraPermission();
  const [facing,    setFacing]    = useState<'back' | 'front'>('back');
  const [zoom,      setZoom]      = useState(1);
  const [torch,     setTorch]     = useState<'off' | 'on'>('off');
  const [mode,      setMode]      = useState<Mode>('live');
  const [streaming, setStreaming] = useState(false);   // 실시간 모드 시작/정지
  const [busy,      setBusy]      = useState(false);   // 사진/파일 처리 중

  useEffect(() => {
    if (!hasPermission) requestPermission();
  }, [hasPermission, requestPermission]);

  const device      = useCameraDevice(facing);
  const cameraRef   = useRef<Camera>(null);
  const sendingRef  = useRef(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const minZoom = device?.minZoom ?? 1;
  const maxZoom = Math.min(device?.maxZoom ?? 8, 8);

  const format = useCameraFormat(device, [
    { videoResolution: { width: 1280, height: 720 } },
  ]);

  const { wsState, result, latencyMs, sendFrame } = useWebSocket(serverUrl);

  // ── 실시간 전송 루프 — streaming & mode가 live일 때만 동작 ───────────────
  const startLoop = useCallback(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (!streaming || mode !== 'live') return;

    intervalRef.current = setInterval(async () => {
      if (sendingRef.current || wsState !== 'connected') return;
      if (!cameraRef.current) return;
      sendingRef.current = true;
      try {
        const snap = await cameraRef.current.takeSnapshot({
          quality: JPEG_QUALITY,
          skipMetadata: true,
        });
        const uri  = snap.path.startsWith('file://') ? snap.path : `file://${snap.path}`;
        const resp = await fetch(uri);
        const buf  = await resp.arrayBuffer();
        sendFrame(new Uint8Array(buf));
      } catch (_) {
      } finally {
        sendingRef.current = false;
      }
    }, SEND_INTERVAL_MS);
  }, [streaming, mode, wsState, sendFrame]);

  useEffect(() => {
    startLoop();
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [startLoop]);

  // ── 모드 전환 (스트리밍 자동 중지) ─────────────────────────────────────
  const changeMode = (next: Mode) => {
    setMode(next);
    setStreaming(false);
    setBusy(false);
    if (intervalRef.current) clearInterval(intervalRef.current);
  };

  // ── 사진 촬영 ────────────────────────────────────────────────────────────
  const capturePhoto = async () => {
    if (busy || wsState !== 'connected' || !cameraRef.current) return;
    setBusy(true);
    try {
      const snap = await cameraRef.current.takeSnapshot({
        quality: JPEG_QUALITY,
        skipMetadata: true,
      });
      const uri  = snap.path.startsWith('file://') ? snap.path : `file://${snap.path}`;
      const resp = await fetch(uri);
      const buf  = await resp.arrayBuffer();
      sendFrame(new Uint8Array(buf));
    } catch (_) {
    } finally {
      setBusy(false);
    }
  };

  // ── 파일 선택 ────────────────────────────────────────────────────────────
  const pickFile = async () => {
    if (wsState !== 'connected') return;
    try {
      const picked = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images,
        quality: 0.7,
        allowsEditing: false,
      });
      if (!picked.canceled && picked.assets[0]) {
        setBusy(true);
        const uri  = picked.assets[0].uri;
        const resp = await fetch(uri);
        const buf  = await resp.arrayBuffer();
        sendFrame(new Uint8Array(buf));
        setBusy(false);
      }
    } catch (_) {
      setBusy(false);
    }
  };

  // ── 권한 미허가 ──────────────────────────────────────────────────────────
  if (!hasPermission) {
    return (
      <SafeAreaView style={styles.fullDark}>
        <View style={styles.centerBox}>
          <Text style={styles.msgText}>카메라 권한이 필요합니다</Text>
          <TouchableOpacity style={styles.primaryBtn} onPress={requestPermission}>
            <Text style={styles.primaryBtnText}>권한 허용</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  if (!device) {
    return (
      <View style={styles.fullDark}>
        <View style={styles.centerBox}>
          <ActivityIndicator size="large" color="#3498DB" />
          <Text style={styles.msgText}>카메라 초기화 중...</Text>
        </View>
      </View>
    );
  }

  const isConnected  = wsState === 'connected';
  const showCamera   = mode !== 'file';
  const showResult   = isConnected && !!result?.frame;

  // ── 레터박스 카메라 크기 (16:9, 전체 너비 기준) ─────────────────────────
  // aspectRatio = width/height. portrait에서 9:16 → 9/16
  // (StyleSheet에서 계산하면 타입 오류 가능성 있어 변수로 분리)
  const camBoxStyle = {
    width: '100%' as const,
    aspectRatio: 9 / 16,
    overflow: 'hidden' as const,
    backgroundColor: '#111',
  };

  return (
    <View style={styles.container}>

      {/* ── 모드 선택 바 ─────────────────────────────────────────────────── */}
      <SafeAreaView style={styles.modeBarWrap}>
        <View style={styles.modeBar}>
          {(Object.keys(MODE_META) as Mode[]).map(m => (
            <TouchableOpacity
              key={m}
              style={[styles.modeBtn, mode === m && styles.modeBtnActive]}
              onPress={() => changeMode(m)}
            >
              <Text style={[styles.modeBtnText, mode === m && styles.modeBtnTextActive]}>
                {MODE_META[m].icon}  {MODE_META[m].label}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      </SafeAreaView>

      {/* ── 카메라 영역 (레터박스) ───────────────────────────────────────── */}
      <View style={styles.cameraOuter}>
        <View style={camBoxStyle}>

          {/* 라이브 카메라 프리뷰 */}
          {showCamera && (
            <Camera
              ref={cameraRef}
              style={StyleSheet.absoluteFill}
              device={device}
              format={format}
              isActive={true}
              photo={true}
              zoom={zoom}
              torch={torch}
            />
          )}

          {/* 서버 분석 결과 프레임 */}
          {showResult && (
            <Image
              source={{ uri: `data:image/jpeg;base64,${result!.frame}` }}
              style={StyleSheet.absoluteFill}
              resizeMode="cover"
              fadeDuration={0}
            />
          )}

          {/* 파일 모드 — 아직 결과 없을 때 안내 */}
          {mode === 'file' && !showResult && (
            <View style={styles.filePlaceholder}>
              <Text style={styles.filePlaceholderIcon}>📁</Text>
              <Text style={styles.filePlaceholderText}>
                아래 버튼을 눌러{'\n'}이미지를 선택하세요
              </Text>
            </View>
          )}

          {/* 실시간 모드 — 시작 전 대기 안내 */}
          {mode === 'live' && !streaming && !showResult && (
            <View style={styles.filePlaceholder}>
              <Text style={styles.filePlaceholderIcon}>🎥</Text>
              <Text style={styles.filePlaceholderText}>
                아래 ▶ 시작 버튼을 눌러{'\n'}실시간 분석을 시작하세요
              </Text>
            </View>
          )}

          {/* OverlayView (연결 상태 + PASS/FAIL 배지 + 폴리곤) */}
          <OverlayView
            result={showResult ? result : null}
            wsState={wsState}
            latencyMs={latencyMs}
            cameraWidth={SCREEN_W}
            cameraHeight={SCREEN_W * 16 / 9}
          />
        </View>
      </View>

      {/* ── 하단 컨트롤 바 ──────────────────────────────────────────────── */}
      <SafeAreaView style={styles.controlBarWrap}>
        <View style={styles.controls}>

          {/* 카메라 반전 (파일 모드 외) */}
          {mode !== 'file' && (
            <TouchableOpacity
              style={styles.ctrlBtn}
              onPress={() => setFacing(f => f === 'back' ? 'front' : 'back')}
            >
              <Text style={styles.ctrlIcon}>🔄</Text>
            </TouchableOpacity>
          )}

          {/* 줌 (카메라 모드만) */}
          {mode !== 'file' && (<>
            <TouchableOpacity
              style={[styles.ctrlBtn, zoom <= minZoom && styles.ctrlBtnDisabled]}
              onPress={() => setZoom(z => Math.max(z - ZOOM_STEP, minZoom))}
              disabled={zoom <= minZoom}
            >
              <Text style={styles.ctrlIcon}>➖</Text>
            </TouchableOpacity>
            <View style={styles.zoomBadge}>
              <Text style={styles.zoomText}>{zoom.toFixed(1)}×</Text>
            </View>
            <TouchableOpacity
              style={[styles.ctrlBtn, zoom >= maxZoom && styles.ctrlBtnDisabled]}
              onPress={() => setZoom(z => Math.min(z + ZOOM_STEP, maxZoom))}
              disabled={zoom >= maxZoom}
            >
              <Text style={styles.ctrlIcon}>➕</Text>
            </TouchableOpacity>
          </>)}

          {/* ── 모드별 메인 액션 버튼 ──────────────────────────────────── */}
          {mode === 'live' && (
            <TouchableOpacity
              style={[
                styles.mainBtn,
                streaming ? styles.mainBtnStop : styles.mainBtnStart,
              ]}
              onPress={() => setStreaming(s => !s)}
            >
              <Text style={styles.mainBtnText}>
                {streaming ? '■  정지' : '▶  시작'}
              </Text>
            </TouchableOpacity>
          )}

          {mode === 'photo' && (
            <TouchableOpacity
              style={[
                styles.mainBtn,
                styles.mainBtnStart,
                (busy || !isConnected) && styles.mainBtnDisabled,
              ]}
              onPress={capturePhoto}
              disabled={busy || !isConnected}
            >
              <Text style={styles.mainBtnText}>
                {busy ? '분석 중...' : '📸  촬영'}
              </Text>
            </TouchableOpacity>
          )}

          {mode === 'file' && (
            <TouchableOpacity
              style={[
                styles.mainBtn,
                styles.mainBtnStart,
                !isConnected && styles.mainBtnDisabled,
              ]}
              onPress={pickFile}
              disabled={!isConnected}
            >
              <Text style={styles.mainBtnText}>
                {busy ? '분석 중...' : '📁  파일 선택'}
              </Text>
            </TouchableOpacity>
          )}

          {/* 토치 (카메라 모드만) */}
          {mode !== 'file' && (
            <TouchableOpacity
              style={[styles.ctrlBtn, torch === 'on' && styles.ctrlBtnActive]}
              onPress={() => setTorch(t => t === 'off' ? 'on' : 'off')}
            >
              <Text style={styles.ctrlIcon}>🔦</Text>
            </TouchableOpacity>
          )}

          {/* 설정 */}
          <TouchableOpacity style={styles.ctrlBtn} onPress={onOpenSettings}>
            <Text style={styles.ctrlIcon}>⚙️</Text>
          </TouchableOpacity>

        </View>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#000',
  },
  fullDark: {
    flex: 1,
    backgroundColor: '#0d0d1a',
  },
  centerBox: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    gap: 16,
  },
  msgText: {
    color: '#fff',
    fontSize: 16,
  },
  primaryBtn: {
    backgroundColor: '#3498DB',
    paddingHorizontal: 24,
    paddingVertical: 12,
    borderRadius: 8,
  },
  primaryBtnText: {
    color: '#fff',
    fontSize: 14,
    fontWeight: 'bold',
  },

  // ── 모드 선택 ────────────────────────────────────────────────────────────
  modeBarWrap: {
    backgroundColor: '#111',
  },
  modeBar: {
    flexDirection: 'row',
    paddingHorizontal: 12,
    paddingVertical: 8,
    gap: 8,
  },
  modeBtn: {
    flex: 1,
    paddingVertical: 8,
    borderRadius: 20,
    alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.08)',
  },
  modeBtnActive: {
    backgroundColor: '#3498DB',
  },
  modeBtnText: {
    color: '#888',
    fontSize: 13,
    fontWeight: '600',
  },
  modeBtnTextActive: {
    color: '#fff',
  },

  // ── 카메라 영역 (레터박스) ───────────────────────────────────────────────
  cameraOuter: {
    flex: 1,
    backgroundColor: '#000',
    justifyContent: 'center',
    alignItems: 'center',
  },

  // ── 파일 모드 안내 ───────────────────────────────────────────────────────
  filePlaceholder: {
    ...StyleSheet.absoluteFillObject,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: '#0d0d1a',
    gap: 12,
  },
  filePlaceholderIcon: {
    fontSize: 48,
  },
  filePlaceholderText: {
    color: '#888',
    fontSize: 15,
    textAlign: 'center',
    lineHeight: 24,
  },

  // ── 하단 컨트롤 ──────────────────────────────────────────────────────────
  controlBarWrap: {
    backgroundColor: 'rgba(0,0,0,0.85)',
  },
  controls: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-around',
    paddingVertical: 12,
    paddingHorizontal: 8,
    gap: 6,
  },
  ctrlBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: 'rgba(255,255,255,0.12)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  ctrlBtnDisabled: {
    opacity: 0.3,
  },
  ctrlBtnActive: {
    backgroundColor: 'rgba(255,200,0,0.35)',
  },
  ctrlIcon: {
    fontSize: 18,
  },
  zoomBadge: {
    paddingHorizontal: 8,
    paddingVertical: 5,
    borderRadius: 8,
    backgroundColor: 'rgba(255,255,255,0.15)',
    minWidth: 44,
    alignItems: 'center',
  },
  zoomText: {
    color: '#fff',
    fontSize: 12,
    fontWeight: 'bold',
    fontVariant: ['tabular-nums'],
  },

  // ── 모드별 메인 버튼 ─────────────────────────────────────────────────────
  mainBtn: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
    maxWidth: 160,
  },
  mainBtnStart: {
    backgroundColor: '#27AE60',
  },
  mainBtnStop: {
    backgroundColor: '#E74C3C',
  },
  mainBtnDisabled: {
    backgroundColor: '#444',
  },
  mainBtnText: {
    color: '#fff',
    fontSize: 15,
    fontWeight: 'bold',
  },
});
