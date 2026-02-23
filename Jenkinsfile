pipeline {
    agent { label 'windows-audio' }

    parameters {
        booleanParam(name: 'RUN_FULL_MATRIX', defaultValue: false,
                     description: 'Run all 9 E2E test matrix entries instead of just the 4 smoke tests')
    }

    environment {
        MODUMB_PYTHON = 'C:\\Users\\John\\modumb\\.venv\\Scripts\\python.exe'
        PYTHONPATH    = "${WORKSPACE}\\src"
    }

    stages {
        stage('Unit Tests') {
            steps {
                bat '%MODUMB_PYTHON% -m pytest tests/ -v --junitxml=reports\\unit-tests.xml'
            }
        }

        stage('Check E2E Needed') {
            steps {
                script {
                    def paths
                    if (env.BRANCH_NAME == 'master') {
                        // Use HEAD~1 for regular commits, but for merges check all
                        // files in the merge by diffing against the first parent
                        def isMerge = bat(script: 'git rev-parse --verify HEAD^2', returnStatus: true) == 0
                        if (isMerge) {
                            paths = bat(script: 'git diff --name-only HEAD^1...HEAD', returnStdout: true).trim()
                        } else {
                            paths = bat(script: 'git diff --name-only HEAD~1...HEAD', returnStdout: true).trim()
                        }
                    } else {
                        paths = bat(script: 'git diff --name-only origin/master...HEAD', returnStdout: true).trim()
                    }
                    def e2eNeeded = paths.split('\n').any { line ->
                        line.trim().matches('src/modumb/(modem|datalink|transport|http|proxy)/.*')
                    }
                    env.E2E_NEEDED = e2eNeeded.toString()
                    echo "Changed files:\n${paths}"
                    echo "E2E needed: ${env.E2E_NEEDED}"
                }
            }
        }

        stage('E2E Smoke Tests') {
            when {
                expression { env.E2E_NEEDED == 'true' || params.RUN_FULL_MATRIX }
            }
            steps {
                lock('vac-audio-devices') {
                    bat '%MODUMB_PYTHON% scripts/test_e2e_vac.py small --baud-rate 1200 --duplex half'
                    bat '%MODUMB_PYTHON% scripts/test_e2e_vac.py small --baud-rate 1200'
                    bat '%MODUMB_PYTHON% scripts/test_e2e_vac.py https --baud-rate 1200 --duplex half'
                    bat '%MODUMB_PYTHON% scripts/test_e2e_vac.py https --baud-rate 1200'
                }
            }
        }

        stage('E2E Full Matrix') {
            when {
                expression { params.RUN_FULL_MATRIX }
            }
            steps {
                lock('vac-audio-devices') {
                    bat '%MODUMB_PYTHON% scripts/test_e2e_vac.py small --baud-rate 300 --duplex half'
                    bat '%MODUMB_PYTHON% scripts/test_e2e_vac.py small --baud-rate 1200 --duplex half'
                    bat '%MODUMB_PYTHON% scripts/test_e2e_vac.py medium --baud-rate 300 --duplex half'
                    bat '%MODUMB_PYTHON% scripts/test_e2e_vac.py medium --baud-rate 1200 --duplex half'
                    bat '%MODUMB_PYTHON% scripts/test_e2e_vac.py small --baud-rate 300'
                    bat '%MODUMB_PYTHON% scripts/test_e2e_vac.py small --baud-rate 1200'
                    bat '%MODUMB_PYTHON% scripts/test_e2e_vac.py medium --baud-rate 1200'
                    // HTTPS tests disabled — known modem-layer demodulation failure
                    // bat '%MODUMB_PYTHON% scripts/test_e2e_vac.py https --baud-rate 1200 --duplex half'
                    // bat '%MODUMB_PYTHON% scripts/test_e2e_vac.py https --baud-rate 1200'
                }
            }
        }
    }

    post {
        always {
            junit allowEmptyResults: true, testResults: 'reports/*.xml'
            cleanWs()
        }
    }
}
