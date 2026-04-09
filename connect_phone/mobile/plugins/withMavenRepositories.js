/**
 * EAS 빌드 서버에서 Maven Central 429 (Too Many Requests) 에러가 발생할 때를 대비해
 * settings.gradle의 pluginManagement.repositories 블록에 JetBrains 미러를 추가한다.
 *
 * 원인: Gradle이 settings 평가 중 expo-autolinking-plugin-shared의 Kotlin 아티팩트
 * (kotlin-util-klib 등)를 Maven Central에서 다운로드할 때 rate limit에 걸리는 현상.
 */
const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

module.exports = function withMavenRepositories(config) {
  return withDangerousMod(config, [
    'android',
    async (config) => {
      const settingsPath = path.join(
        config.modRequest.platformProjectRoot,
        'settings.gradle'
      );

      if (!fs.existsSync(settingsPath)) return config;

      let content = fs.readFileSync(settingsPath, 'utf8');

      // 이미 패치됐으면 스킵
      if (content.includes('cache-redirector.jetbrains.com')) return config;

      // pluginManagement.repositories 안의 gradlePluginPortal() 뒤에 미러 삽입
      // → Maven Central 429 시 JetBrains 캐시로 폴백
      if (content.includes('gradlePluginPortal()')) {
        content = content.replace(
          'gradlePluginPortal()',
          `gradlePluginPortal()
        maven { url "https://cache-redirector.jetbrains.com/maven-central" }`
        );
        fs.writeFileSync(settingsPath, content);
      }

      return config;
    },
  ]);
};
