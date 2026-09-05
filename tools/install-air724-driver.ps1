#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$driverRoot = Join-Path $PSScriptRoot 'vendor\8910_module_usb_driver_signed _20200303_hezhou\DriversForWin10\Drivers'
$logRoot = Join-Path $projectRoot 'logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$logPath = Join-Path $logRoot 'driver-install.log'
$statusPath = Join-Path $logRoot 'driver-install-status.json'
$exitCode = 1
$packages = @()
try {
    $driverNames = @('unisoc_iot', 'sprd_rda', 'unisoc_iot_npi')
    foreach ($driverName in $driverNames) {
        $signature = Get-AuthenticodeSignature -LiteralPath (Join-Path $driverRoot ($driverName + '.cat'))
        if ($signature.Status -ne 'Valid') { throw ('Invalid official driver catalog signature: ' + $driverName) }
    }
    ('Started: ' + (Get-Date -Format o)) | Out-File -LiteralPath $logPath -Encoding utf8
    $exitCode = 0
    foreach ($driverName in $driverNames) {
        & "$env:SystemRoot\System32\pnputil.exe" /add-driver (Join-Path $driverRoot ($driverName + '.inf')) /install 2>&1 |
            Out-File -LiteralPath $logPath -Encoding utf8 -Append
        $packageExitCode = $LASTEXITCODE
        $packages += [pscustomobject]@{ Name = $driverName; ExitCode = $packageExitCode }
        if ($packageExitCode -notin @(0, 3010)) { $exitCode = $packageExitCode }
    }
}
catch {
    $exitCode = 1
    $_ | Out-String | Out-File -LiteralPath $logPath -Encoding utf8 -Append
}
finally {
    [pscustomobject]@{ CompletedAt = (Get-Date -Format o); ExitCode = $exitCode; Packages = $packages } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statusPath -Encoding UTF8
}
exit $exitCode
