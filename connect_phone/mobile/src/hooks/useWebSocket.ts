import { useEffect, useRef, useState, useCallback } from 'react';

export type MatchResult = {
  status: 'pass' | 'fail' | 'error';
  target_id: string | null;
  score: number;
  roi_passed: number;
  roi_total: number;
  corners: [number, number][] | null;  // 0~1 정규화 좌표
  processing_ms: number;
};

export type WsState = 'connecting' | 'connected' | 'disconnected';

const RECONNECT_DELAY_MS = 3000;

export function useWebSocket(serverUrl: string) {
  const wsRef        = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef   = useRef(true);

  const [wsState,  setWsState]  = useState<WsState>('disconnected');
  const [result,   setResult]   = useState<MatchResult | null>(null);
  const [latencyMs, setLatencyMs] = useState<number>(0);

  const connect = useCallback(() => {
    if (!serverUrl || !mountedRef.current) return;

    // https → wss, http → ws 변환
    const wsUrl = serverUrl
      .replace(/^https:\/\//, 'wss://')
      .replace(/^http:\/\//, 'ws://')
      .replace(/\/$/, '') + '/ws';

    console.log('[WS] 연결 시도:', wsUrl);
    setWsState('connecting');

    const ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) return;
      console.log('[WS] 연결 성공');
      setWsState('connected');
    };

    ws.onmessage = (e) => {
      if (!mountedRef.current) return;
      try {
        const data: MatchResult = JSON.parse(e.data as string);
        setResult(data);
        setLatencyMs(data.processing_ms);
      } catch (_) {}
    };

    ws.onclose = () => {
      if (!mountedRef.current) return;
      console.log('[WS] 연결 끊김 — 재연결 예약');
      setWsState('disconnected');
      wsRef.current = null;
      reconnectRef.current = setTimeout(connect, RECONNECT_DELAY_MS);
    };

    ws.onerror = (e) => {
      console.log('[WS] 오류:', e);
    };
  }, [serverUrl]);

  // serverUrl 변경 시 재연결
  useEffect(() => {
    mountedRef.current = true;
    if (reconnectRef.current) clearTimeout(reconnectRef.current);
    wsRef.current?.close();
    if (serverUrl) connect();

    return () => {
      mountedRef.current = false;
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      wsRef.current?.close();
    };
  }, [serverUrl, connect]);

  // JPEG bytes 전송
  const sendFrame = useCallback((jpegBytes: Uint8Array) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(jpegBytes.buffer);
    }
  }, []);

  return { wsState, result, latencyMs, sendFrame };
}
