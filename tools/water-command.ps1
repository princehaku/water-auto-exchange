[CmdletBinding()]
param(
    [ValidatePattern('^COM[1-9][0-9]*$')]
    [string]$Port = 'COM4',
    [ValidateSet('STATUS', 'START', 'FILL', 'DRAIN', 'STOP', 'RESET')]
    [string]$Command = 'STATUS'
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

function Invoke-WaterCommand([string]$Value) {
    $serial.DiscardInBuffer()
    $serial.Write($Value + "`r`n")
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $response = ''
    while ($timer.ElapsedMilliseconds -lt 3000) {
        $response += $serial.ReadExisting()
        $match = [regex]::Match($response, ('(?m)^((?:OK|ERROR) ' + [regex]::Escape($Value) + '(?: [^\r\n]*)?)\r?\n'))
        if ($match.Success) { return $match.Groups[1].Value }
        Start-Sleep -Milliseconds 25
    }
    throw "No water controller response on $Port. Confirm version 0.7.0/0.7.1/0.7.2/0.7.3 is flashed and the USB user port is available."
}

try {
    $serial.Open()
    # Identify the application before sending any command that changes state.
    $status = Invoke-WaterCommand 'STATUS'
    if ($status -notmatch '^OK STATUS project=water_auto_exchange version=0\.7\.[0123](?: |$)') {
        throw 'The selected port did not identify as water_auto_exchange version 0.7.0/0.7.1/0.7.2/0.7.3.'
    }
    Write-Output $status
    if ($Command -ne 'STATUS') {
        $response = Invoke-WaterCommand $Command.ToUpperInvariant()
        Write-Output $response
        if ($response.StartsWith('ERROR ')) { throw $response }
    }
}
finally {
    if ($serial.IsOpen) { $serial.Close() }
    $serial.Dispose()
}
