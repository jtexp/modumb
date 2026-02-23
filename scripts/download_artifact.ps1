$pair = "john:11bb5c963c3fd573d98dbff1cad0646ad4"
$bytes = [System.Text.Encoding]::ASCII.GetBytes($pair)
$base64 = [System.Convert]::ToBase64String($bytes)
$headers = @{Authorization = "Basic $base64"}

$build = $args[0]
$artifact = $args[1]
$outPath = $args[2]

$url = "http://localhost:8090/job/modumb/job/master/$build/artifact/$artifact"
Write-Host "Downloading $url -> $outPath"
Invoke-WebRequest -Uri $url -Headers $headers -UseBasicParsing -OutFile $outPath
Write-Host "Done: $(Get-Item $outPath | Select-Object -ExpandProperty Length) bytes"
