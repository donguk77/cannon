/**
 * EAS 빌드에서 Maven Central(repo.maven.apache.org) 429 rate-limit 문제를 해결하기 위해
 * Gradle init script를 생성하고 gradlew에 --init-script 플래그를 추가한다.
 *
 * init script는 --init-script 플래그를 통해 호출되면 main build 뿐만 아니라
 * includeBuild(expo-gradle-plugin 등)의 settings 평가에도 적용된다.
 * patch-package로 node_modules를 직접 수정하는 것과 이중으로 보완한다.
 */
const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

// Groovy init script: beforeSettings 훅으로 pluginManagement에 미러를 주입
// repo1.maven.org = Sonatype이 직접 운영하는 원본 Maven Central
// (repo.maven.apache.org = Apache 미러와 별도 인프라 → 다른 rate-limit)
const INIT_GRADLE = `
// maven-mirrors.init.gradle
// 자동 생성됨 — plugins/withGradleInit.js
// Maven Central(repo.maven.apache.org)이 HTTP 429를 반환할 때를 대비한 fallback 미러.
// --init-script 플래그로 호출되어 main build 및 모든 includeBuild에 적용된다.

gradle.beforeSettings { settings ->
    try {
        settings.pluginManagement.repositories.maven {
            url 'https://repo1.maven.org/maven2/'
        }
    } catch (ignored) {}
}

allprojects {
    buildscript {
        repositories {
            maven { url 'https://repo1.maven.org/maven2/' }
        }
    }
    repositories {
        maven { url 'https://repo1.maven.org/maven2/' }
    }
}
`.trim();

module.exports = function withGradleInit(config) {
  return withDangerousMod(config, [
    'android',
    async (config) => {
      const projectRoot = config.modRequest.platformProjectRoot;

      // 1. init script 파일 생성
      const initPath = path.join(projectRoot, 'maven-mirrors.init.gradle');
      fs.writeFileSync(initPath, INIT_GRADLE + '\n');

      // 2. gradlew에 --init-script 플래그 추가
      const gradlewPath = path.join(projectRoot, 'gradlew');
      if (!fs.existsSync(gradlewPath)) return config;

      let gradlew = fs.readFileSync(gradlewPath, 'utf8');
      if (gradlew.includes('maven-mirrors.init.gradle')) return config; // 이미 패치됨

      // gradlew의 GradleWrapperMain 실행 라인에 --init-script 삽입
      // 원본:  org.gradle.wrapper.GradleWrapperMain "$@"
      // 수정:  org.gradle.wrapper.GradleWrapperMain --init-script ".../maven-mirrors.init.gradle" "$@"
      gradlew = gradlew.replace(
        'org.gradle.wrapper.GradleWrapperMain "$@"',
        'org.gradle.wrapper.GradleWrapperMain --init-script "$APP_HOME/maven-mirrors.init.gradle" "$@"'
      );

      fs.writeFileSync(gradlewPath, gradlew);
      return config;
    },
  ]);
};
