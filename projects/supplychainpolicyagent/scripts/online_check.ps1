# -*- coding: utf-8 -*-
# Online recheck via UTF-8 byte body (fixes PS5.1 GBK issue)
# 3 quote calls + versions; stop condition TimeoutSec=60 each
$base = 'http://127.0.0.1:8000'
$enc = [System.Text.Encoding]::UTF8

function Post-Quote($bodyObj) {
    $json = ($bodyObj | ConvertTo-Json -Depth 8 -Compress)
    $bytes = $enc.GetBytes($json)
    $resp = Invoke-RestMethod -Method Post -Uri ($base + '/api/tariff/quote') `
        -ContentType 'application/json' -Body $bytes -TimeoutSec 60
    return $resp
}

$r1 = Post-Quote @{
    description = 'electric motorcycle 400W motor'; value_usd = 1000; mode = 'direct';
    incoterm = 'FOB'; origin_country = 'CN'; dest_country = 'US';
    top_k = 8; with_llm = $false
}
Write-Output ("CALL1 direct status=" + $r1.status + " hs=" + $r1.chosen.hs_code + " duty=" + $r1.comprehensive.duty_total + " tax=" + $r1.comprehensive.tariff_amount + " used_llm=" + $r1.used_llm)
Write-Output ("CALL1 components: " + (($r1.comprehensive.components | ForEach-Object { $_.label + '=' + $_.pct }) -join ' | '))
Write-Output ("CALL1 routes: " + (($r1.routes.PSObject.Properties | ForEach-Object { $_.Value.mode + '=' + $_.Value.duty_total }) -join ' '))

$r2 = Post-Quote @{
    description = 'DC motor 500W output'; value_usd = 2000; mode = 'vn';
    incoterm = 'FOB'; origin_country = 'CN'; dest_country = 'US';
    top_k = 8; with_llm = $false
}
Write-Output ("CALL2 vn status=" + $r2.status + " hs=" + $r2.chosen.hs_code + " duty=" + $r2.comprehensive.duty_total + " tax=" + $r2.comprehensive.tariff_amount)

$v = Invoke-RestMethod -Uri ($base + '/api/tariff/quote/versions') -TimeoutSec 15
Write-Output ("VERSIONS extra_version=" + $v.extra_version + " extra_rows=" + $v.extra_rows + " quote_enable=" + $v.quote_enable)

$r2b = Post-Quote @{
    description = 'DC motor 750W output'; value_usd = 1500; mode = 'mx';
    incoterm = 'FOB'; origin_country = 'CN'; dest_country = 'US';
    top_k = 8; with_llm = $false
}
Write-Output ("CALL3 mx status=" + $r2b.status + " hs=" + $r2b.chosen.hs_code + " duty=" + $r2b.comprehensive.duty_total + " compliance_ok=" + $r2b.comprehensive.compliance_ok)
Write-Output "ONLINE_RECHECK_DONE"