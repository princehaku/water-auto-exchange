param(
    [string]$ServerHost = "47.97.255.190",
    [int]$SshPort = 7878,
    [string]$SshUser = "root"
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
$buildDir = Join-Path $projectRoot "build"
New-Item -ItemType Directory -Force $buildDir | Out-Null
$bundle = Join-Path $buildDir "water-deploy.tar.gz"
$remote = "${SshUser}@${ServerHost}"
& tar -czf $bundle -C $projectRoot deploy docs/deployment.md README.md MEMORY.md AGENTS.md
if ($LASTEXITCODE -ne 0) { throw "Archive failed" }
& ssh -p $SshPort $remote "mkdir -p /root/apps/water-auto-exchange"
if ($LASTEXITCODE -ne 0) { throw "Remote directory setup failed" }
& scp -P $SshPort $bundle "${remote}:/root/apps/water-auto-exchange/deploy.tar.gz"
if ($LASTEXITCODE -ne 0) { throw "Upload failed" }
& ssh -p $SshPort $remote "cd /root/apps/water-auto-exchange && tar -xzf deploy.tar.gz && bash deploy/install.sh"
if ($LASTEXITCODE -ne 0) { throw "Deployment failed" }
