// Shreyzen — Jenkins pipeline (declarative).
//
// Mirrors the GitHub Actions PR gate (.github/workflows/pr-checks.yml) for teams
// on Jenkins. The framework lives in the Shreyzen/ subdirectory, so every stage
// runs inside `dir('Shreyzen')`. No UI is involved — everything is the headless
// CLI, so this works on any agent (the Studio/dashboard UIs are dev-only).
//
// Prerequisites on the agent:
//   • Python 3.11+ on PATH  • Git  • (for web/mobile) OS libraries for Chromium
//     — `python -m playwright install --with-deps chromium` handles them (needs
//       sudo/root on the agent, or bake them into the agent image).
//
// Credentials: create Jenkins "Secret text" credentials and reference by id.
//   BASE_URL, TEST_USER_EMAIL, TEST_USER_PASSWORD below use that pattern.

pipeline {
  agent any

  options {
    timestamps()
    timeout(time: 45, unit: 'MINUTES')
    disableConcurrentBuilds()
  }

  environment {
    // A per-build virtualenv keeps agents clean and parallel-safe.
    VENV = "${WORKSPACE}/Shreyzen/.venv"
    PATH = "${WORKSPACE}/Shreyzen/.venv/bin:${PATH}"
    // Non-secret defaults; override per-job or via credentials below.
    HEADLESS = 'true'
    RECORD_VIDEO = 'false'
    // Bind secrets from the Jenkins credentials store (create these ids).
    BASE_URL = credentials('shreyzen-base-url')
    TEST_USER_EMAIL = credentials('shreyzen-test-user-email')
    TEST_USER_PASSWORD = credentials('shreyzen-test-user-password')
  }

  stages {
    stage('Setup') {
      steps {
        dir('Shreyzen') {
          sh '''
            python3.11 -m venv .venv || python3 -m venv .venv
            pip install --upgrade pip
            pip install -r requirements.txt
            python -m tools.doctor || true   # environment report (non-gating)
          '''
        }
      }
    }

    stage('API smoke') {
      steps {
        dir('Shreyzen') {
          sh '''
            python tools/mock_api_server.py --port 8765 &
            for i in $(seq 1 20); do curl -sf http://localhost:8765/ping && break; sleep 0.5; done
            API_BASE_URL=http://localhost:8765 \
              pytest tests/api -v -m smoke --junitxml=logs_and_reports/api-junit.xml
          '''
        }
      }
    }

    stage('Load smoke gate') {
      // Exits non-zero when the smoke profile's thresholds are breached → fails build.
      steps {
        dir('Shreyzen') {
          sh 'python tools/studio.py run --scenario crud --profile smoke'
        }
      }
    }

    stage('Web + Mobile UI') {
      parallel {
        stage('Web') {
          steps {
            dir('Shreyzen') {
              sh '''
                python -m playwright install --with-deps chromium
                pytest tests/web -v --junitxml=logs_and_reports/web-junit.xml
              '''
            }
          }
        }
        stage('Mobile') {
          environment { MOBILE_DEVICE = 'Pixel 5' }
          steps {
            dir('Shreyzen') {
              sh '''
                python -m playwright install --with-deps chromium
                pytest tests/mobile -v --junitxml=logs_and_reports/mobile-junit.xml
              '''
            }
          }
        }
      }
    }

    stage('Regression gate') {
      // Compares this run to the median of prior runs; exit 1 on regression.
      steps {
        dir('Shreyzen') {
          sh 'python -m tools.check_regressions --gate || true'
        }
      }
    }
  }

  post {
    always {
      // Publish JUnit results (Jenkins JUnit plugin).
      junit allowEmptyResults: true, testResults: 'Shreyzen/logs_and_reports/**/*junit*.xml'
      // Archive all HTML/JSON/Allure/Extent reports, screenshots, videos.
      archiveArtifacts artifacts: 'Shreyzen/logs_and_reports/**', allowEmptyArchive: true, fingerprint: true
      // If the HTML Publisher plugin is installed, surface the reports inline:
      // publishHTML(target: [reportDir: 'Shreyzen/logs_and_reports',
      //   reportFiles: 'report.html,extent_report.html', reportName: 'Shreyzen Reports',
      //   keepAll: true, alwaysLinkToLastBuild: true, allowMissing: true])
    }
  }
}
