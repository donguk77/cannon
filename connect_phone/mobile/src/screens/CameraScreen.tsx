import React, { useRef, useState, useEffect, useCallback } from 'react';
import {
  View, TouchableOpacity, Text, StyleSheet,
  Dimensions, SafeAreaView,
} from 'react-native';
import {
  Camera,
  useCameraDevice,
  useCameraPermission,
  useCameraFormat,
} from 'react-native-vision-camera';
import OverlayView from '../components/OverlayView';
import { useWebSocket } from '../hooks/useWebSocket';

const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');

// 서버 전송 주기 (카메라는 30fps 그대로 표시, 서버에는 이 속도로만 전송)
const SEND_INTERVAL_MS = 200; // 5fps → 서버 부하 낮춤
const JPEG_QUALITY     = 50;
const ZOOM_STEP        = 0.5;

type Props = {
  serverUrl: string;
  onOpenSettings: () => void;
};

export default function CameraScreen({ serverUrl, onOpenSettings }: Props) {
  const { hasPermission, requestPermission } = useCameraPermission();
  const [facing,   setFacing]  = useState<'back' | 'front'>('back');
  const [zoom,     setZoom]    = useState(1);
  const [torch,    setTorch]   = useState<'off' | 'on'>('off');

  const device      = useCameraDevice(facing);
  const cameraRef   = useRef<Camera>(null);
  const sendingRef  = useRef(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const minZoom = device?.minZoom ?? 1;
  const maxZoom = Math.min(device?.maxZoom ?? 8, 8);

  // 720p로 고정 (snapshot 속도 / 정확도 균형)
  const format = useCameraFormat(device, [
    { videoResolution: { width: 1280, height: 720 } },
  ]);

  const { wsState, result, latencyMs, sendFrame } = useWebSocket(serverUrl);

  // ── 프레임 전송 루프 ─────────────────────────────────────────────────────
  // 카메라 뷰는 항상 30fps로 표시됨. 여기서는 서버로 보낼 프레임만 제어.
  const startCapture = useCallback(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
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
  }, [wsState, sendFrame]);

  useEffect(() => {
    startCapture();
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [startCapture]);

  // ── 권한 미허가 ──────────────────────────────────────────────────────────
  if (!hasPermission) {
    return (
      <SafeAreaView style={styles.container}>
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
      <View style={styles.container}>
        <View style={styles.centerBox}>
          <Text style={styles.msgText}>카메라를 찾을 수 없습니다</Text>
        </View>
      </View>
    );
  }

  return (
    <View style={styles.container}>

      {/* ── 라이브 카메라 (항상 30fps 표시) ─────────────────────────────── */}
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

      {/* ── 결과 오버레이 (SVG 코너 + PASS/FAIL 배지) ────────────────────── */}
      <OverlayView
        result={result}
        wsState={wsState}
        latencyMs={latencyMs}
        cameraWidth={SCREEN_W}
        cameraHeight={SCREEN_H}
      />

      {/* ── 하단 컨트롤 바 ────────────────────────────────────────────────── */}
      <SafeAreaView style={styles.controlBar} pointerEvents="box-none">
        <View style={styles.controls}>

          {/* 카메라 반전 */}
          <TouchableOpacity
            style={styles.ctrlBtn}
            onPress={() => setFacing(f => f === 'back' ? 'front' : 'back')}
          >
            <Text style={styles.ctrlIcon}>🔄</Text>
          </TouchableOpacity>

          {/* 줌 축소 */}
          <TouchableOpacity
            style={[styles.ctrlBtn, zoom <= minZoom && styles.ctrlBtnDisabled]}
            onPress={() => setZoom(z => Math.max(z - ZOOM_STEP, minZoom))}
            disabled={zoom <= minZoom}
          >
            <Text style={styles.ctrlIcon}>➖</Text>
          </TouchableOpacity>

          {/* 줌 레벨 표시 */}
          <View style={styles.zoomBadge}>
            <Text style={styles.zoomText}>{zoom.toFixed(1)}×</Text>
          </View>

          {/* 줌 확대 */}
          <TouchableOpacity
            style={[styles.ctrlBtn, zoom >= maxZoom && styles.ctrlBtnDisabled]}
            onPress={() => setZoom(z => Math.min(z + ZOOM_STEP, maxZoom))}
            disabled={zoom >= maxZoom}
          >
            <Text style={styles.ctrlIcon}>➕</Text>
          </TouchableOpacity>

          {/* 토치 */}
          <TouchableOpacity
            style={[styles.ctrlBtn, torch === 'on' && styles.ctrlBtnActive]}
            onPress={() => setTorch(t => t === 'off' ? 'on' : 'off')}
          >
            <Text style={styles.ctrlIcon}>🔦</Text>
          </TouchableOpacity>

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
  centerBox: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    gap: 16,
    backgroundColor: '#0d0d1a',
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
  controlBar: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
  },
  controls: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    alignItems: 'center',
    paddingVertical: 14,
    paddingHorizontal: 12,
    backgroundColor: 'rgba(0,0,0,0.6)',
  },
  ctrlBtn: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: 'rgba(255,255,255,0.12)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  ctrlBtnDisabled: {
    opacity: 0.3,
  },
  ctrlBtnActive: {
    backgroundColor: 'rgba(255, 200, 0, 0.35)',
  },
  ctrlIcon: {
    fontSize: 20,
  },
  zoomBadge: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 8,
    backgroundColor: 'rgba(255,255,255,0.15)',
    minWidth: 48,
    alignItems: 'center',
  },
  zoomText: {
    color: '#fff',
    fontSize: 13,
    fontWeight: 'bold',
    fontVariant: ['tabular-nums'],
  },
});
