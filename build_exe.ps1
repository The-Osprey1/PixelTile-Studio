param(
    [switch]$OneFile
)
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    python -m pytest -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    if ($OneFile) {
        python -m PyInstaller --noconfirm --clean --onefile --windowed `
            --name PixelTileStudio `
            --icon assets/pixeltile.ico `
            --collect-all webview `
            --add-data "pixeltile/static;pixeltile/static" `
            --hidden-import PIL._tkinter_finder `
            launcher.py
        Write-Host "`nBuilt single-file EXE: $(Resolve-Path dist\PixelTileStudio.exe)"
    } else {
        python -m PyInstaller --noconfirm --clean pixeltile.spec
        Write-Host "`nBuilt app folder: $(Resolve-Path dist\PixelTileStudio\PixelTileStudio.exe)"
    }
} finally {
    Pop-Location
}
