import React from 'react';
import { View, Text, StyleSheet, Dimensions } from 'react-native';
import Svg, { Polygon, Circle } from 'react-native-svg';
import { MatchResult, WsState } from '../hooks/useWebSocket';

const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');

type Props = {
  result: MatchResult | null;
  wsState: WsState;
  latencyMs: number;
  cameraWidth: number;
  cameraHeight: number;
};

export default function OverlayView({
  result, wsState, latencyMs, cameraWidth, cameraHeight,
}: Props) {

  // 모서리 좌표를 화면 픽셀로 변환
  const cornersToPixels = (corners: [number, number][]) =>
    corners.map(([rx, ry]) => ({
      x: rx * cameraWidth,
      y: ry * cameraHeight,
    }));

  const polygonPoints = result?.corners
    ? cornersToPixels(result.corners).map(p => `${p.x},${p.y}`).join(' ')
    : null;

  const isPass   = result?.status === 'pass';
  const isFail   = result?.status === 'fail';
  const borderColor = isPass ? '#00E676' : isFail ? '#FF1744' : '#888';

  // 연결 상태 색상
  const dotColor =
    wsState === 'connected'   ? '#00E676' :
    wsState === 'connecting'  ? '#FFAB00' : '#FF1744';

  return (
    <View style={StyleSheet.absoluteFill} pointerEvents="none">

      {/* ── 코너 폴리곤 오버레이 ──────────────────────────────────── */}
      {polygonPoints && (
        <Svg style={StyleSheet.absoluteFill}>
          <Polygon
            points={polygonPoints}
            fill="none"
            stroke={borderColor}
            strokeWidth={3}
            strokeLinejoin="round"
          />
          {cornersToPixels(result!.corners!).map((p, i) => (
            <Circle key={i} cx={p.x} cy={p.y} r={6} fill={borderColor} />
          ))}
        </Svg>
      )}

      {/* ── 상단 상태 배너 ────────────────────────────────────────── */}
      <View style={styles.topBanner}>
        {/* 연결 상태 표시 */}
        <View style={styles.connRow}>
          <View style={[styles.dot, { backgroundColor: dotColor }]} />
          <Text style={styles.connText}>
            {wsState === 'connected'   ? '연결됨' :
             wsState === 'connecting'  ? '연결 중...' : '연결 끊김'}
          </Text>
          {wsState === 'connected' && latencyMs > 0 && (
            <Text style={styles.latency}>{latencyMs.toFixed(0)}ms</Text>
          )}
        </View>

        {/* 판정 결과 */}
        {result && wsState === 'connected' && (
          <View style={[styles.badge, { backgroundColor: isPass ? '#00C853' : '#D50000' }]}>
            <Text style={styles.badgeText}>
              {isPass
                ? `✓ PASS  Target ${result.target_id}`
                : '✗ FAIL'}
            </Text>
            {result.roi_total > 0 && (
              <Text style={styles.roiText}>
                ROI {result.roi_passed}/{result.roi_total}  score {result.score}
              </Text>
            )}
          </View>
        )}
      </View>

      {/* ── 전체 화면 테두리 (PASS=초록, FAIL=빨강) ──────────────── */}
      {result && wsState === 'connected' && (
        <View
          style={[
            styles.screenBorder,
            { borderColor, opacity: isPass ? 0.8 : 0.5 },
          ]}
          pointerEvents="none"
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  topBanner: {
    position: 'absolute',
    top: 48,
    left: 12,
    right: 12,
    gap: 6,
  },
  connRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: 'rgba(0,0,0,0.55)',
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 20,
    alignSelf: 'flex-start',
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  connText: {
    color: '#fff',
    fontSize: 12,
    fontWeight: '600',
  },
  latency: {
    color: '#aaa',
    fontSize: 11,
    marginLeft: 4,
  },
  badge: {
    paddingHorizontal: 14,
    paddingVertical: 7,
    borderRadius: 8,
    alignSelf: 'flex-start',
    gap: 2,
  },
  badgeText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: 'bold',
  },
  roiText: {
    color: 'rgba(255,255,255,0.85)',
    fontSize: 11,
  },
  screenBorder: {
    ...StyleSheet.absoluteFillObject,
    borderWidth: 4,
    borderRadius: 4,
  },
});
