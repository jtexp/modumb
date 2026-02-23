$pair = "john:11bb5c963c3fd573d98dbff1cad0646ad4"
$bytes = [System.Text.Encoding]::ASCII.GetBytes($pair)
$base64 = [System.Convert]::ToBase64String($bytes)
$headers = @{Authorization = "Basic $base64"}

$action = $args[0]
$branch = if ($args[1]) { $args[1] } else { "master" }
$baseUrl = "http://localhost:8090/job/modumb/job/$branch"

function Get-CrumbHeaders {
    # Crumbs are tied to the web session in Jenkins 2.x.
    # We must reuse the same WebSession for the crumb request and the POST.
    $session = $null
    $crumb = (Invoke-WebRequest -Uri "http://localhost:8090/crumbIssuer/api/json" `
        -Headers $headers -UseBasicParsing -SessionVariable session | ConvertFrom-Json)
    $postHeaders = @{
        Authorization = "Basic $base64"
        $crumb.crumbRequestField = $crumb.crumb
    }
    return @{ Headers = $postHeaders; Session = $session }
}

function Get-BuildRevision($buildJson) {
    # Extract Git commit SHA from Jenkins build actions (hudson.plugins.git.util.BuildData)
    foreach ($a in $buildJson.actions) {
        if ($a.lastBuiltRevision) {
            return $a.lastBuiltRevision.SHA1
        }
    }
    return $null
}

function Get-QueueBuildNumber($queueUrl) {
    # Poll queue item until Jenkins assigns a build number (up to 60s)
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $q = (Invoke-WebRequest -Uri "$queueUrl/api/json" `
                -Headers $headers -UseBasicParsing | ConvertFrom-Json)
            if ($q.executable) {
                return $q.executable.number
            }
            if ($q.cancelled) {
                Write-Host "Queue item was cancelled"
                return $null
            }
        } catch {
            # Queue item may not exist yet
        }
        Start-Sleep -Seconds 2
    }
    Write-Host "WARNING: Timed out waiting for queue item to get a build number"
    return $null
}

if ($action -eq "run") {
    # Run specific E2E tests by ID or preset.
    # Usage: run <tests> [branch]
    #   <tests>: comma-separated IDs or preset (smoke, full, none)
    $tests = $args[1]
    if (-not $tests) {
        Write-Host "Usage: run <tests> [branch]"
        Write-Host "  <tests>: comma-separated test IDs or preset"
        Write-Host "  Presets: smoke, full, none"
        Write-Host "  IDs: small-300-half, small-1200-half, medium-300-half, medium-1200-half,"
        Write-Host "       small-300-full, small-1200-full, medium-1200-full,"
        Write-Host "       https-1200-half, https-1200-full"
        exit 1
    }
    $runBranch = if ($args[2]) { $args[2] } else { "master" }
    $runUrl = "http://localhost:8090/job/modumb/job/$runBranch"
    $ctx = Get-CrumbHeaders
    $resp = Invoke-WebRequest -Uri "$runUrl/buildWithParameters" -Method POST `
        -Headers $ctx.Headers -WebSession $ctx.Session `
        -Body "json={`"parameter`":[{`"name`":`"E2E_TESTS`",`"value`":`"$tests`"}]}" `
        -ContentType "application/x-www-form-urlencoded" -UseBasicParsing
    Write-Host "Triggered build on $runBranch with E2E_TESTS=$tests (HTTP $($resp.StatusCode))"

    $queueUrl = $resp.Headers["Location"]
    if ($queueUrl) {
        $queueUrl = ($queueUrl -replace '/$','')
        Write-Host "Queue: $queueUrl"
        $buildNum = Get-QueueBuildNumber $queueUrl
        if ($buildNum) {
            Write-Host "Build: #$buildNum"
        }
    }

} elseif ($action -eq "trigger") {
    # Parameterized build — sends E2E_TESTS=full for backward compat.
    # /build returns 400 for parameterized jobs.
    $ctx = Get-CrumbHeaders
    $resp = Invoke-WebRequest -Uri "$baseUrl/buildWithParameters" -Method POST `
        -Headers $ctx.Headers -WebSession $ctx.Session `
        -Body "json={`"parameter`":[{`"name`":`"E2E_TESTS`",`"value`":`"full`"}]}" `
        -ContentType "application/x-www-form-urlencoded" -UseBasicParsing
    Write-Host "Triggered build on $branch with E2E_TESTS=full (HTTP $($resp.StatusCode))"

    # Resolve queue item to build number so callers know exactly which build to poll.
    # Jenkins returns a Location header pointing to the queue item (e.g. .../queue/item/42/).
    $queueUrl = $resp.Headers["Location"]
    if ($queueUrl) {
        $queueUrl = ($queueUrl -replace '/$','')
        Write-Host "Queue: $queueUrl"
        $buildNum = Get-QueueBuildNumber $queueUrl
        if ($buildNum) {
            Write-Host "Build: #$buildNum"
        }
    }

} elseif ($action -eq "poll") {
    # Poll a specific build or lastBuild until it finishes.
    # Usage: poll <branch> [interval] [buildNum]
    $interval = if ($args[2]) { [int]$args[2] } else { 15 }
    $buildId = if ($args[3]) { $args[3] } else { "lastBuild" }
    $revShown = $false
    Write-Host "Polling $baseUrl/$buildId every ${interval}s..."
    while ($true) {
        try {
            $json = (Invoke-WebRequest -Uri "$baseUrl/$buildId/api/json" `
                -Headers $headers -UseBasicParsing | ConvertFrom-Json)
            $num = $json.number
            $building = $json.building
            $result = $json.result
            $display = $json.displayName
            # Once we know the real build number, pin to it
            if ($buildId -eq "lastBuild") { $buildId = $num }
            # Show the Git revision once (available after SCM checkout)
            if (-not $revShown) {
                $rev = Get-BuildRevision $json
                if ($rev) {
                    Write-Host "Commit: $rev"
                    $revShown = $true
                }
            }
            if ($building) {
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Build #$num ($display) still running..."
            } else {
                # Final check for revision if we never got it while building
                if (-not $revShown) {
                    $rev = Get-BuildRevision $json
                    if ($rev) { Write-Host "Commit: $rev" }
                }
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Build #$num ($display) finished: $result"
                Write-Host ""
                (Invoke-WebRequest -Uri "$baseUrl/$num/consoleText" `
                    -Headers $headers -UseBasicParsing).Content
                exit $(if ($result -eq "SUCCESS") { 0 } else { 1 })
            }
        } catch {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error checking status: $_"
        }
        Start-Sleep -Seconds $interval
    }

} elseif ($action -eq "scan") {
    # Trigger a multibranch index/scan and wait for Jenkins to build the expected commit.
    # This is the normal way to kick off builds — push your commits, then scan.
    # Jenkins discovers the new commit and builds it automatically.
    # Usage: scan [branch] [timeout_secs] [expected_commit]
    #   timeout_secs: how long to wait for the build (default 60)
    #   expected_commit: if provided, verify the build matches this commit SHA

    $timeout = if ($args[2]) { [int]$args[2] } else { 60 }
    $expectCommit = if ($args[3]) { $args[3] } else { $null }

    # Check if the latest build already covers the expected commit.
    $prevBuild = $null
    try {
        $prev = (Invoke-WebRequest -Uri "$baseUrl/lastBuild/api/json" `
            -Headers $headers -UseBasicParsing | ConvertFrom-Json)
        $prevBuild = $prev.number
        $prevRev = Get-BuildRevision $prev
        Write-Host "Last build: #$prevBuild | Commit: $(if ($prevRev) { $prevRev } else { 'n/a' }) | Result: $($prev.result)"
        if ($expectCommit -and $prevRev -and $prevRev.StartsWith($expectCommit.Substring(0, [Math]::Min(10, $expectCommit.Length)))) {
            if (-not $prev.building) {
                Write-Host "Already built commit $expectCommit -> Build #$prevBuild ($($prev.result))"
                exit $(if ($prev.result -eq "SUCCESS") { 0 } else { 1 })
            } else {
                Write-Host "Build #$prevBuild is already running for this commit"
                Write-Host "Use: poll $branch 15 $prevBuild"
                exit 0
            }
        }
    } catch {
        Write-Host "No previous builds for $branch (new branch)"
    }

    # The correct endpoint is /indexing/build — NOT /build (which returns 403).
    $ctx = Get-CrumbHeaders
    try {
        Invoke-WebRequest -Uri "http://localhost:8090/job/modumb/indexing/build" `
            -Method POST -Headers $ctx.Headers -WebSession $ctx.Session `
            -UseBasicParsing | Out-Null
    } catch {
        # Jenkins often closes the connection after triggering the scan.
        # A "connection was closed" error is normal — the scan still runs.
        if ($_.Exception.Message -match "closed") {
            # expected
        } else {
            throw
        }
    }
    Write-Host "Multibranch scan triggered"

    # Wait for a build matching the expected commit, or any new build.
    Write-Host "Waiting up to ${timeout}s for build on $branch..."
    $deadline = (Get-Date).AddSeconds($timeout)
    $foundBuild = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        try {
            $cur = (Invoke-WebRequest -Uri "$baseUrl/lastBuild/api/json" `
                -Headers $headers -UseBasicParsing | ConvertFrom-Json)
            $curRev = Get-BuildRevision $cur
            # Match by commit if we have an expected commit, otherwise by build number
            if ($expectCommit -and $curRev) {
                if ($curRev.StartsWith($expectCommit.Substring(0, [Math]::Min(10, $expectCommit.Length)))) {
                    $foundBuild = $cur
                    break
                }
            } elseif (($null -eq $prevBuild) -or ($cur.number -gt $prevBuild)) {
                $foundBuild = $cur
                break
            }
        } catch {
            # Branch job may not exist yet if scan is still running
        }
    }
    if ($foundBuild) {
        $rev = Get-BuildRevision $foundBuild
        $revStr = if ($rev) { " | Commit: $rev" } else { "" }
        $statusStr = if ($foundBuild.building) { "running" } else { $foundBuild.result }
        Write-Host "Build: #$($foundBuild.number) | Status: $statusStr$revStr"
        if ($foundBuild.building) {
            Write-Host "Use: poll $branch 15 $($foundBuild.number)"
        }
    } else {
        Write-Host "No matching build within ${timeout}s (Jenkins may not have found changes)"
    }

} elseif ($action -eq "status") {
    $json = (Invoke-WebRequest -Uri "$baseUrl/lastBuild/api/json" `
        -Headers $headers -UseBasicParsing | ConvertFrom-Json)
    $rev = Get-BuildRevision $json
    $revStr = if ($rev) { $rev.Substring(0, 10) } else { "n/a" }
    Write-Host "Build #$($json.number) | Building: $($json.building) | Result: $($json.result) | Commit: $revStr"

} else {
    # Default: fetch console text for a build number or lastBuild
    $buildNum = if ($args[0]) { $args[0] } else { "lastBuild" }
    (Invoke-WebRequest -Uri "$baseUrl/$buildNum/consoleText" `
        -Headers $headers -UseBasicParsing).Content
}
