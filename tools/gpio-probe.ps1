[CmdletBinding()]
param(
    [ValidatePattern('^COM[1-9][0-9]*$')]
    [string]$Port = 'COM4',
    [switch]$Continuous,
    [ValidateRange(15, 600)]
    [int]$TimeoutSeconds = 20
)

$ErrorActionPreference = 'Stop'
$serial = New-Object System.IO.Ports.SerialPort
$serial.PortName = $Port.ToUpperInvariant()
$serial.BaudRate = 115200
$serial.DataBits = 8
$serial.Parity = [IO.Ports.Parity]::None
$serial.StopBits = [IO.Ports.StopBits]::One
$serial.Handshake = [IO.Ports.Handshake]::None
$serial.DtrEnable = $false
$serial.RtsEnable = $false
$serial.WriteTimeout = 1000
$serial.ReadTimeout = 1000
$serial.NewLine = "`n"
$taskRoot = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $taskRoot 'logs'
[IO.Directory]::CreateDirectory($logDirectory) | Out-Null
$logPath = Join-Path $logDirectory ('gpio-probe-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
$script:probeBuffer = ''
$started = $false
$completed = $false
$leaveRunning = $false

function Write-ProbeLine([string]$Line) {
    $entry = (Get-Date -Format 'HH:mm:ss.fff') + ' ' + $Line
    Add-Content -LiteralPath $logPath -Value $entry -Encoding UTF8
    Write-Output $entry
}

function Read-ProbeLines {
    $script:probeBuffer += $serial.ReadExisting()
    while ($script:probeBuffer.Contains("`n")) {
        $index = $script:probeBuffer.IndexOf("`n")
        $line = $script:probeBuffer.Substring(0, $index).TrimEnd("`r")
        $script:probeBuffer = $script:probeBuffer.Substring($index + 1)
        if ($line.Length -gt 0) { $line }
    }
}

try {
    $serial.Open()
    $serial.DiscardInBuffer()
    $serial.Write("STATUS`r`n")
    $checkTimer = [Diagnostics.Stopwatch]::StartNew()
    $identified = $false
    $alreadyRunning = $false
    $initialCycle = 0
    while ($checkTimer.Elapsed.TotalSeconds -lt 4 -and -not $identified) {
        foreach ($line in @(Read-ProbeLines)) {
            Write-ProbeLine $line
            if ($line -match '^OK STATUS project=gk21_motor_test version=0\.2\.5 ' -and
                $line -match ' state=STANDBY ' -and
                $line -match ' probe_total=1 ' -and
                $line -match ' probe_cycle_ms=10000 ' -and
                $line -match ' probe_candidates=5(?: |$)' -and
                ($line -match ' probe_state=(IDLE|DONE|STOPPED)(?: |$)' -or
                 ($Continuous -and $line -match ' probe_state=RUNNING ' -and $line -match ' probe_continuous=1 '))) {
                $identified = $true
                $alreadyRunning = $line -match ' probe_state=RUNNING '
                if ($alreadyRunning -and $line -match ' probe_cycle=(\d+)(?: |$)') {
                    $initialCycle = [int]$Matches[1]
                }
            }
        }
        if (-not $identified) { Start-Sleep -Milliseconds 25 }
    }
    if (-not $identified) { throw 'Expected gk21_motor_test v0.2.5 toggling only GPIO5, with a 10-second cycle; use -Continuous to observe its automatic loop.' }
    $modeLabel = if ($Continuous) { 'continuous' } else { 'one pass' }
    $started = $true
    if ($alreadyRunning) {
        Write-ProbeLine 'HOST observing automatic loop: GPIO5 only; HIGH 5 seconds / LOW 5 seconds; 10 seconds per cycle.'
    }
    else {
        Write-ProbeLine ('HOST starting ' + $modeLabel + ': GPIO5 only; HIGH 5 seconds / LOW 5 seconds; 10 seconds per cycle.')
        if ($Continuous) { $serial.Write("PROBE LOOP`r`n") }
        else { $serial.Write("PROBE`r`n") }
    }
    $runTimer = [Diagnostics.Stopwatch]::StartNew()
    $nextStatus = 11.0
    while ($runTimer.Elapsed.TotalSeconds -lt $TimeoutSeconds -and -not $completed) {
        foreach ($line in @(Read-ProbeLines)) {
            Write-ProbeLine $line
            if ($line -match '^ERROR ' -or $line -match '^PROBE .*state=ERROR') {
                throw ('Board diagnostic error: ' + $line)
            }
            if ($line -match '^OK STATUS .* probe_state=DONE(?: |$)') {
                if ($Continuous) { throw 'Continuous mode ended unexpectedly.' }
                $completed = $true
            }
            if ($line -match '^OK STATUS .* probe_state=(ERROR|STOPPED)(?: |$)') {
                throw ('Probe stopped before completion: ' + $line)
            }
        }
        if (-not $completed -and $runTimer.Elapsed.TotalSeconds -ge $nextStatus) {
            $serial.Write("STATUS`r`n")
            $nextStatus += 5.0
        }
        Start-Sleep -Milliseconds 25
    }
    if ($Continuous) {
        $serial.Write("STATUS`r`n")
        $finalTimer = [Diagnostics.Stopwatch]::StartNew()
        while ($finalTimer.Elapsed.TotalSeconds -lt 3 -and -not $leaveRunning) {
            foreach ($line in @(Read-ProbeLines)) {
                Write-ProbeLine $line
                if ($line -match '^OK STATUS .* probe_state=RUNNING ' -and
                    $line -match ' probe_continuous=1 ' -and
                    $line -match ' probe_cycle=(\d+)(?: |$)') {
                    $leaveRunning = [int]$Matches[1] -ge [Math]::Max(2, $initialCycle + 1)
                }
            }
            if (-not $leaveRunning) { Start-Sleep -Milliseconds 25 }
        }
        if (-not $leaveRunning) { throw 'Could not verify continuous scanning advanced to the next cycle.' }
        Write-ProbeLine 'HOST continuous scan remains RUNNING on the board; LuaTools logs continue every 5 seconds. Send STOP to end it.'
    }
    else {
        if (-not $completed) { throw 'Probe did not report DONE before the host timeout.' }
        Write-ProbeLine 'HOST one pass completed; no automatic repeat.'
    }
}
finally {
    if ($serial.IsOpen) {
        if ($started -and -not $leaveRunning) {
            try {
                $serial.Write("STOP`r`n")
                $stopTimer = [Diagnostics.Stopwatch]::StartNew()
                $stopAck = $false
                while ($stopTimer.Elapsed.TotalSeconds -lt 3 -and -not $stopAck) {
                    foreach ($line in @(Read-ProbeLines)) {
                        Write-ProbeLine $line
                        if ($line -match '^OK STOP ') { $stopAck = $true }
                    }
                    if (-not $stopAck) { Start-Sleep -Milliseconds 25 }
                }
                if (-not $stopAck) { Write-Warning 'No STOP acknowledgement; disconnect board power if needed.' }
            }
            catch { Write-Warning ('STOP delivery failed: ' + $_.Exception.Message) }
        }
        $serial.Close()
    }
    $serial.Dispose()
    Write-Output ('Log: ' + $logPath)
}
