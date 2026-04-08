import React, { useState, useEffect } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { StatusBar } from 'expo-status-bar';
import CameraScreen from './src/screens/CameraScreen';
import SettingsScreen from './src/screens/SettingsScreen';

const STORAGE_KEY = '@canon_server_url';

export default function App() {
  const [serverUrl,    setServerUrl]    = useState<string>('');
  const [showSettings, setShowSettings] = useState<boolean>(false);
  const [loaded,       setLoaded]       = useState<boolean>(false);

  // 저장된 URL 로드
  useEffect(() => {
    AsyncStorage.getItem(STORAGE_KEY).then(url => {
      if (url) setServerUrl(url);
      else     setShowSettings(true);   // 첫 실행: 설정 화면 자동 표시
      setLoaded(true);
    });
  }, []);

  const handleSaveUrl = async (url: string) => {
    await AsyncStorage.setItem(STORAGE_KEY, url);
    setServerUrl(url);
    setShowSettings(false);
  };

  if (!loaded) return null;

  return (
    <>
      <StatusBar style="light" />
      {showSettings || !serverUrl ? (
        <SettingsScreen
          currentUrl={serverUrl}
          onSave={handleSaveUrl}
        />
      ) : (
        <CameraScreen
          serverUrl={serverUrl}
          onOpenSettings={() => setShowSettings(true)}
        />
      )}
    </>
  );
}
