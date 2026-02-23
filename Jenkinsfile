pipeline {
    agent { label 'windows-audio' }

    parameters {
        string(name: 'E2E_TESTS', defaultValue: '',
               description: 'E2E tests: comma-separated IDs (e.g. small-1200-half,medium-300-half), preset (smoke/full/none), or empty for auto-detect')
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

        stage('Resolve E2E Tests') {
            steps {
                script {
                    def registry = [
                        'small-300-half':   'small --baud-rate 300 --duplex half',
                        'small-1200-half':  'small --baud-rate 1200 --duplex half',
                        'medium-300-half':  'medium --baud-rate 300 --duplex half',
                        'medium-1200-half': 'medium --baud-rate 1200 --duplex half',
                        'small-300-full':   'small --baud-rate 300',
                        'small-1200-full':  'small --baud-rate 1200',
                        'medium-1200-full': 'medium --baud-rate 1200',
                        'https-1200-half':  'https --baud-rate 1200 --duplex half',
                        'https-1200-full':  'https --baud-rate 1200',
                    ]
                    def presets = [
                        'smoke': ['small-1200-half', 'small-1200-full', 'https-1200-half', 'https-1200-full'],
                        'full':  registry.keySet().toList(),
                    ]

                    def param = params.E2E_TESTS?.trim() ?: ''
                    def testIds = []

                    if (param == 'none') {
                        testIds = []
                    } else if (presets.containsKey(param)) {
                        testIds = presets[param]
                    } else if (param) {
                        testIds = param.split(',').collect { it.trim() }
                        def invalid = testIds.findAll { !registry.containsKey(it) }
                        if (invalid) {
                            error "Unknown E2E test IDs: ${invalid.join(', ')}\nValid IDs: ${registry.keySet().sort().join(', ')}"
                        }
                    } else {
                        // Auto-detect from changed files
                        def paths
                        if (env.BRANCH_NAME == 'master') {
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
                        echo "Changed files:\n${paths}"
                        echo "E2E needed: ${e2eNeeded}"
                        if (e2eNeeded) {
                            testIds = presets['smoke']
                        }
                    }

                    def commands = testIds.collect { registry[it] }
                    env.E2E_COMMANDS = commands.join('\n')
                    env.E2E_TEST_IDS = testIds.join(',')
                    env.E2E_TEST_COUNT = testIds.size().toString()
                    echo "E2E tests (${env.E2E_TEST_COUNT}): ${env.E2E_TEST_IDS}"
                }
            }
        }

        stage('E2E Tests') {
            when {
                expression { env.E2E_TEST_COUNT.toInteger() > 0 }
            }
            steps {
                lock('vac-audio-devices') {
                    script {
                        def ids = env.E2E_TEST_IDS.split(',')
                        def cmds = env.E2E_COMMANDS.split('\n')
                        for (int i = 0; i < cmds.size(); i++) {
                            echo "Running E2E test ${i + 1}/${cmds.size()}: ${ids[i]}"
                            bat "%MODUMB_PYTHON% scripts/test_e2e_vac.py ${cmds[i]}"
                        }
                    }
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
