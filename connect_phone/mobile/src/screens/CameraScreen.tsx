import React, { useRef, useState, useEffect, useCallback } from 'react';
import {
  View, TouchableOpacity, Text, StyleSheet,
  Dimensions, SafeAreaView, Image,
} from 'react-native';
import {
  Camera,
  useCameraDevice,
  useCameraPermission,
  useCameraFormat,
} from 'react-native-vision-camera';
import { useWebSocket } from '../hooks/useWebSocket';

const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');

// 전송 간격 (ms) — takeSnapshot은 takePictureAsync보다 훨씬 빠름
const SEND_INTERVAL_MS = 150;   // ~6fps
const JPEG_QUALITY     = 50;    // 0~100

type Props = {
  serverUrl: string;
  onOpenSettings: () => void;
};

export default function CameraScreen({ serverUrl, onOpenSettings }: Props) {
  const { hasPermission, requestPermission } = useCameraPermission();
  const [facing, setFacing]  = useState<'back' | 'front'>('back');
  const device               = useCameraDevice(facing);
  const cameraRef            = useRef<Camera>(null);
  const isSendingRef         = useRef(false);
  const intervalRef          = useRef<ReturnType<typeof setInterval> | null>(null);

  // 캡처 해상도 낮춰서 속도 향상
  const format = useCameraFormat(device, [
    { videoResolution: { width: 1280, height: 720 } },
  ]);

  const { wsState, result, latencyMs, sendFrame } = useWebSocket(serverUrl);

  // 주기적으로 스냅샷 캡처 → 전송
  const startCapture = useCallback(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    intervalRef.current = setInterval(async () => {
      if (isSendingRef.current || wsState !== 'connected') return;
      if (!cameraRef.current) return;

      isSendingRef.current = true;
      try {
        // takeSnapshot: 셔터 없이 현재 프레임 버퍼에서 즉시 캡처 (takePictureAsync보다 훨씬 빠름)
        const snapshot = await cameraRef.current.takeSnapshot({
          quality: JPEG_QUALITY,
          skipMetadata: true,
        });

        // 파일 경로 → ArrayBuffer → Uint8Array
        const uri  = snapshot.path.startsWith('file://') ? snapshot.path : `file://${snapshot.path}`;
        const resp = await fetch(uri);
        const buf  = await resp.arrayBuffer();
        sendFrame(new Uint8Array(buf));
      } catch (_) {
      } finally {
        isSendingRef.current = false;
      }
    }, SEND_INTERVAL_MS);
  }, [wsState, sendFrame]);

  useEffect(() => {
    startCapture();
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [startCapture]);

  // 권한 미허가
  if (!hasPermission) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.permBox}>
          <Text style={styles.permText}>카메라 권한이 필요합니다</Text>
          <TouchableOpacity style={styles.permBtn} onPress={requestPermission}>
            <Text style={styles.permBtnText}>권한 허용</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  if (!device) {
    return (
      <View style={styles.container}>
        <Text style={{ color: '#fff', textAlign: 'center', marginTop: 100 }}>
          카메라를 찾을 수 없습니다
        </Text>
      </View>
    );
  }

  const isConnected = wsState === 'connected';
  const isPass      = result?.status === 'pass';
  const dotColor    = wsState === 'connected'  ? '#00E676'
                    : wsState === 'connecting' ? '#FFAB00' : '#FF1744';
  const connLabel   = wsState === 'connected'  ? '연결됨'
                    : wsState === 'connecting' ? '연결 중...' : '연결 끊김';

  return (
    <View style={styles.container}>

      {/* ── 캡처 전용 카메라 (화면에 보이지 않음) ── */}
      <Camera
        ref={cameraRef}
        style={styles.hiddenCamera}
        device={device}
        format={format}
        isActive={true}
        photo={true}
      />

      {/* ── 메인 디스플레이: 서버 분석 결과 프레임 ── */}
      {result?.frame && isConnected ? (
        <Image
          source={{ uri: `data:image/jpeg;base64,${result.frame}` }}
          style={styles.serverFrame}
          resizeMode="contain"
          fadeDuration={0}
        />
      ) : (
        <View style={styles.waitScreen}>
          <View style={[styles.dot, { backgroundColor: dotColor }]} />
          <Text style={styles.waitText}>{connLabel}</Text>
          {wsState === 'connected' && (
            <Text style={styles.waitSub}>서버 분석 대기 중...</Text>
          )}
        </View>
      )}

      {/* ── 상단 상태 바 ── */}
      <SafeAreaView style={styles.topBar} pointerEvents="none">
        <View style={styles.statusRow}>
          <View style={[styles.dot, { backgroundColor: dotColor }]} />
          <Text style={styles.statusText}>{connLabel}</Text>
          {isConnected && latencyMs > 0 && (
            <Text style={styles.latency}>{latencyMs.toFixed(0)}ms</Text>
          )}
          {result && isConnected && (
            <View style={[styles.badge, { backgroundColor: isPass ? '#00C853' : '#D50000' }]}>
              <Text style={styles.badgeText}>
                {isPass ? `✓ PASS  Target ${result.target_id}` : '✗ FAIL'}
              </Text>
            </View>
          )}
        </View>
      </SafeAreaView>

      {/* ── 하단 컨트롤 바 ── */}
      <SafeAreaView style={styles.controlBar} pointerEvents="box-none">
        <View style={styles.controls}>
          <TouchableOpacity
            style={styles.ctrlBtn}
            onPress={() => setFacing(f => f === 'back' ? 'front' : 'back')}
          >
            <Text style={styles.ctrlIcon}>🔄</Text>
          </TouchableOpacity>
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
  hiddenCamera: {
    position: 'absolute',
    width: SCREEN_W,
    height: SCREEN_H,
    opacity: 0,
  },
  serverFrame: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: '#000',
  },
  waitScreen: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    gap: 12,
    backgroundColor: '#0d0d1a',
  },
  waitText: {
    color: '#fff',
    fontSize: 18,
    fontWeight: '600',
  },
  waitSub: {
    color: '#888',
    fontSize: 13,
  },
  topBar: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: 'rgba(0,0,0,0.55)',
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  statusText: {
    color: '#fff',
    fontSize: 12,
    fontWeight: '600',
  },
  latency: {
    color: '#aaa',
    fontSize: 11,
  },
  badge: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 6,
  },
  badgeText: {
    color: '#fff',
    fontSize: 13,
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
    paddingVertical: 16,
    paddingHorizontal: 40,
    backgroundColor: 'rgba(0,0,0,0.55)',
  },
  ctrlBtn: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: 'rgba(255,255,255,0.12)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  ctrlIcon: {
    fontSize: 22,
  },
  permBox: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    gap: 16,
    backgroundColor: '#0d0d1a',
  },
  permText: {
    color: '#fff',
    fontSize: 16,
  },
  permBtn: {
    backgroundColor: '#3498DB',
    paddingHorizontal: 24,
    paddingVertical: 12,
    borderRadius: 8,
  },
  permBtnText: {
    color: '#fff',
    fontSize: 14,
    fontWeight: 'bold',
  },
});
