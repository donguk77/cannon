import React, { useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, SafeAreaView, KeyboardAvoidingView, Platform,
} from 'react-native';

type Props = {
  currentUrl: string;
  onSave: (url: string) => void;
};

export default function SettingsScreen({ currentUrl, onSave }: Props) {
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
          PC에서 start.bat 실행 후{'\n'}
          터미널에 출력된 URL을 입력하세요.
        </Text>

        {/* URL 예시 안내 */}
        <View style={styles.exampleBox}>
          <Text style={styles.exampleLabel}>URL 예시</Text>
          <Text style={styles.exampleText}>https://xxxx.trycloudflare.com</Text>
          <Text style={styles.exampleText}>http://192.168.0.10:8765  (같은 WiFi)</Text>
        </View>

        <Text style={styles.label}>서버 URL</Text>
        <TextInput
          style={styles.input}
          value={url}
          onChangeText={setUrl}
          placeholder="https://xxxx.trycloudflare.com"
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
  exampleBox: {
    backgroundColor: '#1a1a2e',
    borderRadius: 8,
    padding: 14,
    marginBottom: 24,
    gap: 4,
  },
  exampleLabel: {
    color: '#3498DB',
    fontSize: 11,
    fontWeight: '700',
    marginBottom: 4,
    textTransform: 'uppercase',
    letterSpacing: 0.8,
  },
  exampleText: {
    color: '#aaa',
    fontSize: 13,
    fontFamily: Platform.OS === 'ios' ? 'Courier' : 'monospace',
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
