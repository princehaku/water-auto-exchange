[CmdletBinding()]
param(
    [ValidatePattern('^COM[1-9][0-9]*$')]
    [string]$Port = 'COM4',
    [ValidateSet('STATUS', 'START', 'STOP')]
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

function Invoke-MotorCommand([string]$Value) {
    $serial.DiscardInBuffer()
    $serial.Write($Value + "`r`n")
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $response = ''
    while ($timer.ElapsedMilliseconds -lt 3000) {
        $response += $serial.ReadExisting()
        $match = [regex]::Match($response, ('(?m)^((?:OK|ERROR) ' + $Value + '(?: [^\r\n]*)?)\r?\n'))
        if ($match.Success) { return $match.Groups[1].Value }
        Start-Sleep -Milliseconds 25
    }
    throw "No motor test response on $Port. Confirm the test script is flashed and LuaTools has released the AT port."
}

try {
    $serial.Open()
    $status = Invoke-MotorCommand 'STATUS'
    if ($status -notmatch '^OK STATUS project=gk21_motor_test ') {
        throw 'The selected port did not identify as gk21_motor_test.'
    }
    Write-Output $status
    if ($Command -ne 'STATUS') {
        $response = Invoke-MotorCommand $Command.ToUpperInvariant()
        Write-Output $response
        if ($response.StartsWith('ERROR ')) { throw $response }
    }
}
finally {
    if ($serial.IsOpen) { $serial.Close() }
    $serial.Dispose()
}
