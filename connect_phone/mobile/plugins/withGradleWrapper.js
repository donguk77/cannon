const { withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

const GRADLE_VERSION = '8.13';

module.exports = function withGradleWrapper(config) {
  return withDangerousMod(config, [
    'android',
    async (config) => {
      const propertiesPath = path.join(
        config.modRequest.platformProjectRoot,
        'gradle/wrapper/gradle-wrapper.properties'
      );

      if (fs.existsSync(propertiesPath)) {
        let content = fs.readFileSync(propertiesPath, 'utf8');
        content = content.replace(
          /distributionUrl=.+/,
          `distributionUrl=https\\://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip`
        );
        fs.writeFileSync(propertiesPath, content);
      }

      return config;
    },
  ]);
};
