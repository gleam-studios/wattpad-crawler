$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (Get-Command py -ErrorAction SilentlyContinue) {
  py -3 package_app.py @args
} else {
  python package_app.py @args
}
