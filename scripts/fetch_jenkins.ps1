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

if ($action -eq "trigger") {
    # Parameterized build — must use buildWithParameters endpoint.
    # /build returns 400 for parameterized jobs.
    $ctx = Get-CrumbHeaders
    $resp = Invoke-WebRequest -Uri "$baseUrl/buildWithParameters" -Method POST `
        -Headers $ctx.Headers -WebSession $ctx.Session `
        -Body "json={`"parameter`":[{`"name`":`"RUN_FULL_MATRIX`",`"value`":true}]}" `
        -ContentType "application/x-www-form-urlencoded" -UseBasicParsing
    Write-Host "Triggered build on $branch (HTTP $($resp.StatusCode))"

} elseif ($action -eq "poll") {
    # Poll a specific build or lastBuild until it finishes.
    # Usage: poll <branch> [interval] [buildNum]
    $interval = if ($args[2]) { [int]$args[2] } else { 15 }
    $buildId = if ($args[3]) { $args[3] } else { "lastBuild" }
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
            if ($building) {
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Build #$num ($display) still running..."
            } else {
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
    # Trigger a multibranch index/scan.
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

} elseif ($action -eq "status") {
    $json = (Invoke-WebRequest -Uri "$baseUrl/lastBuild/api/json" `
        -Headers $headers -UseBasicParsing | ConvertFrom-Json)
    Write-Host "Build #$($json.number) | Building: $($json.building) | Result: $($json.result)"

} else {
    # Default: fetch console text for a build number or lastBuild
    $buildNum = if ($args[0]) { $args[0] } else { "lastBuild" }
    (Invoke-WebRequest -Uri "$baseUrl/$buildNum/consoleText" `
        -Headers $headers -UseBasicParsing).Content
}
