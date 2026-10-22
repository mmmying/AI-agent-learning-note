param(
    [string]$Python = "python",
    [switch]$DatabaseOnly,
    [switch]$Coverage
)

$ErrorActionPreference = "Stop"
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $pytestArgs = @("-m", "pytest")
    if ($DatabaseOnly) { $pytestArgs += @("-m", "database") }
    if ($Coverage) {
        $pytestArgs += @("--cov", "--cov-report=term-missing", "--cov-report=xml", "--junitxml=junit.xml")
    }
    & $Python @pytestArgs
    $testExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $testExitCode
