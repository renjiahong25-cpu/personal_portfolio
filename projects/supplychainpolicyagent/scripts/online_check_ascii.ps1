# online recheck, ASCII-only source; Chinese built from Unicode code points at runtime
# to avoid PS5.1 reading the file as GBK
$enc = [System.Text.Encoding]::UTF8

function CN([int[]]$codes) {
    ($codes | ForEach-Object { [char]$_ }) -join ''
}

# "26寸电动摩托车 电机400W"
$desc1 = '26' + (CN 0x5BF8) + (CN 0x7535 0x52A8 0x6469 0x6258 0x8F66) + ' ' + (CN 0x7535 0x673A) + '400W'
# "直流电动机 输出功率500W"
$desc2 = (CN 0x76F4 0x6D41 0x7535 0x52A8 0x673A) + ' ' + (CN 0x8F93 0x51FA 0x529F 0x7387) + '500W'
# "直流电动机 输出功率750W"
$desc3 = (CN 0x76F4 0x6D41 0x7535 0x52A8 0x673A) + ' ' + (CN 0x8F93 0x51FA 0x529F 0x7387) + '750W'

function Post-Quote($bodyObj) {
    $json = ($bodyObj | ConvertTo-Json -Depth 8 -Compress)
    $bytes = $enc.GetBytes($json)
    $resp = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/tariff/quote' `
        -ContentType 'application/json' -Body $bytes -TimeoutSec 60
    return $resp
}

Write-Output ("SENT1=" + $desc1)
Write-Output ("SENT2=" + $desc2)
Write-Output ("SENT3=" + $desc3)

$r1 = Post-Quote @{
    description = $desc1; value_usd = 1000; mode = 'direct';
    incoterm = 'FOB'; origin_country = 'CN'; dest_country = 'US';
    top_k = 8; with_llm = $false
}
Write-Output ("CALL1 direct status=" + $r1.status + " hs=" + $r1.chosen.hs_code + " duty=" + $r1.comprehensive.duty_total + " tax=" + $r1.comprehensive.tariff_amount + " used_llm=" + $r1.used_llm)
Write-Output ("CALL1 components: " + (($r1.comprehensive.components | ForEach-Object { $_.label + '=' + $_.pct }) -join ' | '))
Write-Output ("CALL1 routes: " + (($r1.routes.PSObject.Properties | ForEach-Object { $_.Value.mode + '=' + $_.Value.duty_total }) -join ' '))

$r2 = Post-Quote @{
    description = $desc2; value_usd = 2000; mode = 'vn';
    incoterm = 'FOB'; origin_country = 'CN'; dest_country = 'US';
    top_k = 8; with_llm = $false
}
Write-Output ("CALL2 vn status=" + $r2.status + " hs=" + $r2.chosen.hs_code + " duty=" + $r2.comprehensive.duty_total + " tax=" + $r2.comprehensive.tariff_amount)

$v = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/tariff/quote/versions' -TimeoutSec 15
Write-Output ("VERSIONS extra_version=" + $v.extra_version + " extra_rows=" + $v.extra_rows + " quote_enable=" + $v.quote_enable)

$r2b = Post-Quote @{
    description = $desc3; value_usd = 1500; mode = 'mx';
    incoterm = 'FOB'; origin_country = 'CN'; dest_country = 'US';
    top_k = 8; with_llm = $false
}
Write-Output ("CALL3 mx status=" + $r2b.status + " hs=" + $r2b.chosen.hs_code + " duty=" + $r2b.comprehensive.duty_total + " compliance_ok=" + $r2b.comprehensive.compliance_ok)
Write-Output "ONLINE_RECHECK_DONE"