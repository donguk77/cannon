import React, { useState, useEffect } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { View, Text, ActivityIndicator, StyleSheet } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import CameraScreen from './src/screens/CameraScreen';
import SettingsScreen from './src/screens/SettingsScreen';
import { autoDiscoverServer } from './src/utils/discovery';

const STORAGE_KEY = '@canon_server_url';

export default function App() {
  const [serverUrl,    setServerUrl]    = useState('');
  const [showSettings, setShowSettings] = useState(false);
  const [scanning,     setScanning]     = useState(false);
  const [ready,        setReady]        = useState(false);

  useEffect(() => {
    (async () => {
      const saved = await AsyncStorage.getItem(STORAGE_KEY);
      if (saved) {
        setServerUrl(saved);
        setReady(true);
      } else {
        // 첫 실행: 자동 검색
        setScanning(true);
        setReady(true);
        const found = await autoDiscoverServer();
        if (found) {
          await AsyncStorage.setItem(STORAGE_KEY, found);
          setServerUrl(found);
        } else {
          setShowSettings(true);
        }
        setScanning(false);
      }
    })();
  }, []);

  const handleSaveUrl = async (url: string) => {
    await AsyncStorage.setItem(STORAGE_KEY, url);
    setServerUrl(url);
    setShowSettings(false);
  };

  const handleScan = async () => {
    setShowSettings(false);
    setScanning(true);
    const found = await autoDiscoverServer();
    if (found) {
      await AsyncStorage.setItem(STORAGE_KEY, found);
      setServerUrl(found);
    } else {
      setShowSettings(true);
    }
    setScanning(false);
  };

  if (!ready || scanning) {
    return (
      <View style={styles.scanScreen}>
        <StatusBar style="light" />
        <ActivityIndicator size="large" color="#3498DB" />
        <Text style={styles.scanText}>서버 자동 검색 중...</Text>
        <Text style={styles.scanSub}>같은 WiFi의 PC 서버를 찾고 있습니다</Text>
      </View>
    );
  }

  if (showSettings || !serverUrl) {
    return (
      <>
        <StatusBar style="light" />
        <SettingsScreen
          currentUrl={serverUrl}
          onSave={handleSaveUrl}
          onScan={handleScan}
        />
      </>
    );
  }

  return (
    <>
      <StatusBar style="light" />
      <CameraScreen
        serverUrl={serverUrl}
        onOpenSettings={() => setShowSettings(true)}
      />
    </>
  );
}

const styles = StyleSheet.create({
  scanScreen: {
    flex: 1,
    backgroundColor: '#0d0d1a',
    justifyContent: 'center',
    alignItems: 'center',
    gap: 16,
  },
  scanText: {
    color: '#fff',
    fontSize: 18,
    fontWeight: '600',
  },
  scanSub: {
    color: '#888',
    fontSize: 13,
  },
});
