/**
 * 메인 android/settings.gradle의 pluginManagement.repositories에
 * repo1.maven.org 미러를 추가한다.
 *
 * 참고: 이 플러그인은 메인 settings.gradle만 수정한다.
 * includeBuild(expo-gradle-plugin)의 settings는 patch-package(patches/ 디렉토리)로,
 * 모든 빌드 전반에는 withGradleInit.js(init script)로 처리한다.
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
      if (content.includes('repo1.maven.org')) return config;

      // pluginManagement.repositories 안의 gradlePluginPortal() 뒤에 미러 삽입
      if (content.includes('gradlePluginPortal()')) {
        content = content.replace(
          'gradlePluginPortal()',
          `gradlePluginPortal()
        maven { url "https://repo1.maven.org/maven2/" }`
        );
        fs.writeFileSync(settingsPath, content);
      }

      return config;
    },
  ]);
};
