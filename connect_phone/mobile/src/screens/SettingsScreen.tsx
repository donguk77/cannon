import React, { useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, SafeAreaView, KeyboardAvoidingView, Platform,
} from 'react-native';

type Props = {
  currentUrl: string;
  onSave: (url: string) => void;
  onScan: () => void;
};

export default function SettingsScreen({ currentUrl, onSave, onScan }: Props) {
  const [url, setUrl] = useState(currentUrl);

  const handleSave = () => {
    const trimmed = url.trim().replace(/\/$/, '');
    if (!trimmed) return;
    onSave(trimmed);
  };

  return (
    <SafeAreaView style={styles.container}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        style={styles.inner}
      >
        <Text style={styles.title}>서버 연결 설정</Text>
        <Text style={styles.subtitle}>
          PC에서 서버를 실행한 후 자동 검색을 누르세요.{'\n'}
          같은 WiFi에 연결되어 있어야 합니다.
        </Text>

        {/* 자동 검색 버튼 */}
        <TouchableOpacity style={styles.scanBtn} onPress={onScan}>
          <Text style={styles.scanIcon}>🔍</Text>
          <Text style={styles.scanBtnText}>자동 검색</Text>
        </TouchableOpacity>

        <View style={styles.divider}>
          <View style={styles.dividerLine} />
          <Text style={styles.dividerText}>또는 직접 입력</Text>
          <View style={styles.dividerLine} />
        </View>

        <Text style={styles.label}>서버 URL</Text>
        <TextInput
          style={styles.input}
          value={url}
          onChangeText={setUrl}
          placeholder="http://192.168.1.50:8765"
          placeholderTextColor="#555"
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
        />

        <TouchableOpacity
          style={[styles.saveBtn, !url.trim() && styles.saveBtnDisabled]}
          onPress={handleSave}
          disabled={!url.trim()}
        >
          <Text style={styles.saveBtnText}>저장 후 연결</Text>
        </TouchableOpacity>

        <Text style={styles.hint}>
          저장하면 다음 앱 실행 시 자동으로 연결됩니다.
        </Text>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0d0d1a',
  },
  inner: {
    flex: 1,
    padding: 28,
    justifyContent: 'center',
  },
  title: {
    color: '#fff',
    fontSize: 24,
    fontWeight: 'bold',
    marginBottom: 8,
  },
  subtitle: {
    color: '#888',
    fontSize: 14,
    lineHeight: 22,
    marginBottom: 24,
  },
  scanBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#1e3a5f',
    borderWidth: 1,
    borderColor: '#3498DB',
    borderRadius: 12,
    paddingVertical: 16,
    gap: 10,
    marginBottom: 24,
  },
  scanIcon: {
    fontSize: 20,
  },
  scanBtnText: {
    color: '#3498DB',
    fontSize: 17,
    fontWeight: 'bold',
  },
  divider: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginBottom: 20,
  },
  dividerLine: {
    flex: 1,
    height: 1,
    backgroundColor: '#333',
  },
  dividerText: {
    color: '#555',
    fontSize: 12,
  },
  label: {
    color: '#ccc',
    fontSize: 13,
    marginBottom: 6,
    fontWeight: '600',
  },
  input: {
    backgroundColor: '#1e1e30',
    color: '#fff',
    borderWidth: 1,
    borderColor: '#3498DB55',
    borderRadius: 8,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 15,
    marginBottom: 20,
  },
  saveBtn: {
    backgroundColor: '#3498DB',
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: 'center',
    marginBottom: 14,
  },
  saveBtnDisabled: {
    backgroundColor: '#333',
  },
  saveBtnText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: 'bold',
  },
  hint: {
    color: '#555',
    fontSize: 12,
    textAlign: 'center',
  },
});
