[CmdletBinding()]
param(
    [ValidatePattern('^COM[1-9][0-9]*$')]
    [string]$Port
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $projectRoot 'logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logFile = Join-Path $logDirectory ('air724-probe-{0}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))

function Write-Log([string]$Message) {
    $Message | Add-Content -LiteralPath $logFile -Encoding UTF8
    Write-Host $Message
}

function Write-ProbeWarning([string]$Message) {
    ('WARNING: ' + $Message) | Add-Content -LiteralPath $logFile -Encoding UTF8
    Write-Warning $Message
}

function Read-AtResponse($Serial, [int]$TimeoutMilliseconds = 2500) {
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $response = New-Object System.Text.StringBuilder
    while ($timer.ElapsedMilliseconds -lt $TimeoutMilliseconds) {
        $chunk = $Serial.ReadExisting()
        if ($chunk.Length -gt 0) {
            [void]$response.Append($chunk)
            $value = $response.ToString()
            if ($value -match '(?m)^\s*OK\s*$') {
                return [pscustomobject]@{ Status = 'OK'; Text = $value }
            }
            if ($value -match '(?m)^\s*(ERROR|\+CME ERROR:.*|\+CMS ERROR:.*)\s*$') {
                return [pscustomobject]@{ Status = 'ERROR'; Text = $value }
            }
        }
        Start-Sleep -Milliseconds 25
    }
    return [pscustomobject]@{ Status = 'TIMEOUT'; Text = $response.ToString() }
}

Write-Log ('Started: {0}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'))
Write-Log ('Log: {0}' -f $logFile)

if (-not $Port) {
    Write-Log 'Listing current COM devices. No port will be opened.'
    $devices = @()
    try {
        $devices = @(Get-CimInstance -ClassName Win32_PnPEntity -Filter "PNPClass = 'Ports' AND Present = TRUE" -ErrorAction Stop |
            Where-Object { $_.Name -match '\(COM[1-9][0-9]*\)' })
    }
    catch {
        Write-ProbeWarning ('Cannot query COM device details (permission or WMI failure): {0}. Falling back to SerialPort.GetPortNames().' -f $_.Exception.Message)
    }

    if ($devices.Count -gt 0) {
        foreach ($device in ($devices | Sort-Object Name)) {
            Write-Log ('Device: {0}' -f $device.Name)
            Write-Log ('  PNPDeviceID: {0}' -f $device.PNPDeviceID)
        }
    }
    else {
        try {
            $portNames = @([System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object)
            if ($portNames.Count -eq 0) { Write-Log 'No current COM ports found.' }
            foreach ($portName in $portNames) {
                Write-Log ('Device: {0}; PNPDeviceID: unavailable' -f $portName)
            }
        }
        catch {
            Write-ProbeWarning ('Cannot enumerate COM port names: {0}' -f $_.Exception.Message)
        }
    }
    Write-Log 'To query a confirmed AT port, run this script with -Port COMx.'
    return
}

$serial = $null
try {
    $serial = New-Object System.IO.Ports.SerialPort
    $serial.PortName = $Port.ToUpperInvariant()
    $serial.BaudRate = 115200
    $serial.DataBits = 8
    $serial.Parity = [System.IO.Ports.Parity]::None
    $serial.StopBits = [System.IO.Ports.StopBits]::One
    $serial.Handshake = [System.IO.Ports.Handshake]::None
    $serial.DtrEnable = $false
    $serial.RtsEnable = $false
    $serial.ReadTimeout = 500
    $serial.WriteTimeout = 1000
    Write-Log ('Opening {0}: 115200 8N1, no handshake, DTR=false, RTS=false.' -f $serial.PortName)
    $serial.Open()

    foreach ($command in @('AT', 'ATI', 'AT+CGMI', 'AT+CGMM', 'AT+CGMR')) {
        $serial.DiscardInBuffer()
        Write-Log ('TX: {0}' -f $command)
        $serial.Write($command + "`r")
        $result = Read-AtResponse -Serial $serial
        if ($result.Text.Length -gt 0) { Write-Log ('RX: ' + $result.Text.Trim()) }
        Write-Log ('Result: {0}' -f $result.Status)
        if ($result.Status -ne 'OK') {
            Write-ProbeWarning 'Query failed or timed out. Stopping without sending further commands.'
            break
        }
    }
}
catch {
    Write-ProbeWarning ('Probe failed: {0}' -f $_.Exception.Message)
}
finally {
    if ($null -ne $serial) {
        if ($serial.IsOpen) { $serial.Close() }
        $serial.Dispose()
        Write-Log 'Serial port released.'
    }
}
