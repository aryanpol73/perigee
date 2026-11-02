# Builds the signed Android app on this machine: android/dist/perigee.apk (install it
# on a phone) and perigee.aab (for Google Play). Needs JDK 17 and the Android SDK
# (Android Studio installs both). The signing key and its password live outside the
# repository, in %USERPROFILE%\.perigee-android (created once; see docs/mobile-app.md).
#   powershell -ExecutionPolicy Bypass -File android\build-apk.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$keys = Join-Path $env:USERPROFILE ".perigee-android"
$keystore = Join-Path $keys "perigee-release.jks"
if (-not (Test-Path $keystore)) { throw "No signing key at $keystore (docs/mobile-app.md, 'Signing key')." }
if (-not $env:ANDROID_HOME) { $env:ANDROID_HOME = Join-Path $env:LOCALAPPDATA "Android\Sdk" }
$env:PERIGEE_KEYSTORE = $keystore
$env:PERIGEE_KEYSTORE_PASSWORD = (Get-Content (Join-Path $keys "keystore-password.txt") -Raw).Trim()
try {
  Push-Location $here
  & .\gradlew.bat --no-daemon assembleRelease bundleRelease
  if ($LASTEXITCODE -ne 0) { throw "Gradle failed ($LASTEXITCODE)" }
  New-Item -ItemType Directory -Force dist | Out-Null
  Copy-Item app\build\outputs\apk\release\app-release.apk dist\perigee.apk -Force
  Copy-Item app\build\outputs\bundle\release\app-release.aab dist\perigee.aab -Force
  Write-Output "Built $(Join-Path $here 'dist\perigee.apk') and perigee.aab"
} finally {
  Pop-Location
  Remove-Item Env:PERIGEE_KEYSTORE_PASSWORD -ErrorAction SilentlyContinue
}
