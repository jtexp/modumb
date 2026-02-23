$pair = "john:11bb5c963c3fd573d98dbff1cad0646ad4"
$bytes = [System.Text.Encoding]::ASCII.GetBytes($pair)
$base64 = [System.Convert]::ToBase64String($bytes)
$headers = @{Authorization = "Basic $base64"}

$action = $args[0]
$branch = if ($args[1]) { $args[1] } else { "worktree-cicd" }
$baseUrl = "http://localhost:8090/job/modumb/job/$branch"

if ($action -eq "trigger") {
    $crumb = (Invoke-WebRequest -Uri "http://localhost:8090/crumbIssuer/api/json" -Headers $headers -UseBasicParsing | ConvertFrom-Json)
    $headers[$crumb.crumbRequestField] = $crumb.crumb
    Invoke-WebRequest -Uri "$baseUrl/build?delay=0" -Method POST -Headers $headers -Body "json={`"parameter`":[{`"name`":`"RUN_FULL_MATRIX`",`"value`":true}]}" -ContentType "application/x-www-form-urlencoded" -UseBasicParsing | Select-Object StatusCode
} elseif ($action -eq "poll") {
    $interval = if ($args[2]) { [int]$args[2] } else { 15 }
    Write-Host "Polling $baseUrl/lastBuild every ${interval}s..."
    while ($true) {
        try {
            $json = (Invoke-WebRequest -Uri "$baseUrl/lastBuild/api/json" -Headers $headers -UseBasicParsing | ConvertFrom-Json)
            $num = $json.number
            $building = $json.building
            $result = $json.result
            $display = $json.displayName
            if ($building) {
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Build #$num ($display) still running..."
            } else {
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Build #$num ($display) finished: $result"
                Write-Host ""
                (Invoke-WebRequest -Uri "$baseUrl/$num/consoleText" -Headers $headers -UseBasicParsing).Content
                exit $(if ($result -eq "SUCCESS") { 0 } else { 1 })
            }
        } catch {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Error checking status: $_"
        }
        Start-Sleep -Seconds $interval
    }
} elseif ($action -eq "scan") {
    $crumbJson = (Invoke-WebRequest -Uri "http://localhost:8090/crumbIssuer/api/json" -Headers $headers -UseBasicParsing | ConvertFrom-Json)
    $scanHeaders = @{
        Authorization = "Basic $base64"
        $crumbJson.crumbRequestField = $crumbJson.crumb
    }
    Invoke-WebRequest -Uri "http://localhost:8090/job/modumb/build" -Method POST -Headers $scanHeaders -UseBasicParsing | Out-Null
    Write-Host "Multibranch scan triggered"
} elseif ($action -eq "status") {
    $json = (Invoke-WebRequest -Uri "$baseUrl/lastBuild/api/json" -Headers $headers -UseBasicParsing | ConvertFrom-Json)
    Write-Host "Build #$($json.number) | Building: $($json.building) | Result: $($json.result)"
} else {
    $buildNum = if ($args[0]) { $args[0] } else { "lastBuild" }
    (Invoke-WebRequest -Uri "$baseUrl/$buildNum/consoleText" -Headers $headers -UseBasicParsing).Content
}
