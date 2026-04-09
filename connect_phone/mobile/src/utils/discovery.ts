import * as Network from 'expo-network';

const WS_PORT = 8765;
const PROBE_TIMEOUT_MS = 800;

/** 단일 호스트에 WebSocket 연결 시도. 성공하면 'http://host:port' 반환, 실패시 null */
function probeHost(host: string): Promise<string | null> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (val: string | null) => {
      if (!settled) { settled = true; resolve(val); }
    };

    const timer = setTimeout(() => finish(null), PROBE_TIMEOUT_MS);

    try {
      const ws = new WebSocket(`ws://${host}:${WS_PORT}/ws`);
      ws.onopen  = () => { clearTimeout(timer); ws.close(); finish(`http://${host}:${WS_PORT}`); };
      ws.onerror = () => { clearTimeout(timer); finish(null); };
    } catch {
      clearTimeout(timer);
      finish(null);
    }
  });
}

/**
 * 같은 WiFi 서브넷 전체를 스캔해서 WebSocket 서버를 찾는다.
 * 찾으면 'http://ip:port' 반환, 못 찾으면 null
 */
export async function autoDiscoverServer(): Promise<string | null> {
  try {
    const ip = await Network.getIpAddressAsync();
    if (!ip || ip === '0.0.0.0' || ip === '127.0.0.1') return null;

    const parts = ip.split('.');
    if (parts.length !== 4) return null;

    const base = parts.slice(0, 3).join('.');
    const hosts = Array.from({ length: 254 }, (_, i) => `${base}.${i + 1}`)
                       .filter(h => h !== ip);

    // 전체 동시 탐색 — 첫 성공이 즉시 반환
    return await new Promise<string | null>((resolve) => {
      let pending = hosts.length;
      let won = false;

      for (const host of hosts) {
        probeHost(host).then(result => {
          if (won) return;
          if (result) {
            won = true;
            resolve(result);
          } else {
            pending--;
            if (pending === 0) resolve(null);
          }
        });
      }
    });
  } catch {
    return null;
  }
}
