import React, { useRef, useState, useEffect, useCallback } from 'react';
import {
  View, TouchableOpacity, Text, StyleSheet,
  Dimensions, SafeAreaView,
} from 'react-native';
import { CameraView, useCameraPermissions, CameraType } from 'expo-camera';
import { useWebSocket } from '../hooks/useWebSocket';
import OverlayView from '../components/OverlayView';

const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');

// 전송 간격 (ms) — 낮출수록 FPS 높아지지만 서버 부하 증가
const SEND_INTERVAL_MS = 300;   // ~3fps
const JPEG_QUALITY     = 0.35;  // 0.0~1.0 (낮을수록 작은 파일)

type Props = {
  serverUrl: string;
  onOpenSettings: () => void;
};

export default function CameraScreen({ serverUrl, onOpenSettings }: Props) {
  const [permission, requestPermission] = useCameraPermissions();
  const [facing, setFacing]   = useState<CameraType>('back');
  const cameraRef             = useRef<CameraView>(null);
  const isSendingRef          = useRef(false);
  const intervalRef           = useRef<ReturnType<typeof setInterval> | null>(null);

  const { wsState, result, latencyMs, sendFrame } = useWebSocket(serverUrl);

  // 주기적으로 프레임 캡처 → 전송
  const startCapture = useCallback(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    intervalRef.current = setInterval(async () => {
      if (isSendingRef.current || wsState !== 'connected') return;
      if (!cameraRef.current) return;

      isSendingRef.current = true;
      try {
        const photo = await cameraRef.current.takePictureAsync({
          base64: false,
          quality: JPEG_QUALITY,
          skipProcessing: true,
          exif: false,
        });
        if (!photo?.uri) return;

        // uri → fetch → ArrayBuffer → Uint8Array
        const resp  = await fetch(photo.uri);
        const buf   = await resp.arrayBuffer();
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
  if (!permission) return <View style={styles.container} />;
  if (!permission.granted) {
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

  return (
    <View style={styles.container}>
      <CameraView
        ref={cameraRef}
        style={StyleSheet.absoluteFill}
        facing={facing}
      />

      {/* 결과 오버레이 */}
      <OverlayView
        result={result}
        wsState={wsState}
        latencyMs={latencyMs}
        cameraWidth={SCREEN_W}
        cameraHeight={SCREEN_H}
      />

      {/* 하단 컨트롤 바 */}
      <SafeAreaView style={styles.controlBar} pointerEvents="box-none">
        <View style={styles.controls}>
          {/* 전후면 전환 */}
          <TouchableOpacity
            style={styles.ctrlBtn}
            onPress={() => setFacing(f => f === 'back' ? 'front' : 'back')}
          >
            <Text style={styles.ctrlIcon}>🔄</Text>
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
    backgroundColor: 'rgba(0,0,0,0.45)',
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
