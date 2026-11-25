#!/bin/bash
# ==============================================================================
#  CynoType — Build standalone macOS app (.app) and drag-and-drop installer (.dmg)
#  Output:
#    dist/CynoType.app
#    dist/CynoType-Installer.dmg
# ==============================================================================
set -e
cd "$(dirname "$0")"

APP_NAME="CynoType"
VERSION="2.1.0"
BUNDLE_ID="com.cynotype.autotyper"
DMG_NAME="CynoType-Installer.dmg"
VOL_NAME="Install CynoType"

[ -x .venv/bin/python ] || ./setup_mac.sh

if ! .venv/bin/python -c "import PyInstaller" >/dev/null 2>&1; then
  echo "==> Installing PyInstaller..."
  .venv/bin/python -m pip install pyinstaller
fi

echo "==> Generating DMG installer background..."
.venv/bin/python - << 'PYEOF'
import os, AppKit, Quartz, CoreText, Foundation

W, H = 660, 420
SCALE = 2
PW, PH = W * SCALE, H * SCALE
cs = Quartz.CGColorSpaceCreateDeviceRGB()
ctx = Quartz.CGContext = Quartz.CGBitmapContextCreate(
    None, PW, PH, 8, PW * 4, cs, Quartz.kCGImageAlphaPremultipliedLast
)
Quartz.CGContextScaleCTM(ctx, SCALE, SCALE)

# Clean Apple light neutral background (#F5F5F7)
Quartz.CGContextSetRGBFillColor(ctx, 0.961, 0.961, 0.969, 1.0)
Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, W, H))

# Subtle frosted center glow
grad = Quartz.CGGradientCreateWithColorComponents(
    cs,
    (1.0, 1.0, 1.0, 0.85, 0.961, 0.961, 0.969, 0.0),
    (0.0, 1.0),
    2,
)
Quartz.CGContextDrawRadialGradient(
    ctx, grad,
    Quartz.CGPointMake(W / 2.0, H / 2.0 + 10), 0.0,
    Quartz.CGPointMake(W / 2.0, H / 2.0 + 10), 310.0,
    0,
)

# Minimal thin right-pointing arrow centered between CynoType (x=180) and Applications (x=480)
ay = 218.0
Quartz.CGContextSetRGBStrokeColor(ctx, 0.114, 0.114, 0.122, 0.28)
Quartz.CGContextSetLineWidth(ctx, 2.2)
Quartz.CGContextSetLineCap(ctx, Quartz.kCGLineCapRound)
Quartz.CGContextSetLineJoin(ctx, Quartz.kCGLineJoinRound)

Quartz.CGContextBeginPath(ctx)
Quartz.CGContextMoveToPoint(ctx, 298.0, ay)
Quartz.CGContextAddLineToPoint(ctx, 362.0, ay)
Quartz.CGContextMoveToPoint(ctx, 350.0, ay + 10.0)
Quartz.CGContextAddLineToPoint(ctx, 362.0, ay)
Quartz.CGContextAddLineToPoint(ctx, 350.0, ay - 10.0)
Quartz.CGContextStrokePath(ctx)

# Clean SF Pro helper text at bottom
def draw_centered_text(text, y, size, weight, alpha):
    Quartz.CGContextSetTextMatrix(ctx, Quartz.CGAffineTransformIdentity)
    font = AppKit.NSFont.systemFontOfSize_weight_(size, weight)
    color = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.114, 0.114, 0.122, alpha)
    attrs = {
        AppKit.NSFontAttributeName: font,
        AppKit.NSForegroundColorAttributeName: color,
    }
    ns_str = Foundation.NSAttributedString.alloc().initWithString_attributes_(text, attrs)
    line = CoreText.CTLineCreateWithAttributedString(ns_str)
    width = CoreText.CTLineGetTypographicBounds(line, None, None, None)[0]
    x = (W - width) / 2.0
    Quartz.CGContextSetTextPosition(ctx, x, y)
    CoreText.CTLineDraw(line, ctx)

draw_centered_text("Drag CynoType to Applications", 58.0, 14.0, AppKit.NSFontWeightMedium, 0.72)
draw_centered_text("Then open CynoType from your Applications folder", 36.0, 11.5, AppKit.NSFontWeightRegular, 0.44)

out_path = os.path.abspath("assets/dmg_background.png")
out_url = AppKit.NSURL.fileURLWithPath_(out_path)
dest = Quartz.CGImageDestinationCreateWithURL(out_url, "public.png", 1, None)
# Set 144 DPI metadata so Finder treats it as Retina @2x (660x420pt)
props = {
    Quartz.kCGImagePropertyDPIWidth: 144.0,
    Quartz.kCGImagePropertyDPIHeight: 144.0,
}
Quartz.CGImageDestinationAddImage(dest, Quartz.CGBitmapContextCreateImage(ctx), props)
Quartz.CGImageDestinationFinalize(dest)
print("Generated:", out_path)
PYEOF

echo "==> Building ${APP_NAME}.app with PyInstaller..."
rm -rf "build/${APP_NAME}" "dist/${APP_NAME}" "dist/${APP_NAME}.app" "dist/${DMG_NAME}"

.venv/bin/pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "${APP_NAME}" \
  --icon "assets/CynoType.icns" \
  --osx-bundle-identifier "${BUNDLE_ID}" \
  --add-data "assets:assets" \
  --hidden-import cynotype.engine \
  --hidden-import AppKit \
  --hidden-import Foundation \
  --hidden-import Quartz \
  --hidden-import CoreFoundation \
  --hidden-import PyObjCTools.AppHelper \
  --hidden-import objc \
  --hidden-import pynput.keyboard._darwin \
  --hidden-import pynput.mouse._darwin \
  --hidden-import pyperclip \
  --exclude-module mcp \
  --exclude-module playwright \
  auto_typer.py

# Clean up intermediate standalone folder and generated spec (keep the .app bundle)
rm -rf "dist/${APP_NAME}" "${APP_NAME}.spec"

echo "==> Updating Info.plist metadata..."
PLIST="dist/${APP_NAME}.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName ${APP_NAME}" "$PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string ${APP_NAME}" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString ${VERSION}" "$PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string ${VERSION}" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion ${VERSION}" "$PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string ${VERSION}" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSAccessibilityUsageDescription string CynoType needs Accessibility permission to simulate realistic keystrokes into your active window." "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSInputMonitoringUsageDescription string CynoType uses Input Monitoring so Esc and F8 can immediately stop typing from any app." "$PLIST" 2>/dev/null || true

echo "==> Ad-hoc code signing ${APP_NAME}.app..."
codesign --force --deep --sign - "dist/${APP_NAME}.app"

echo "==> Packaging ${DMG_NAME}..."
STAGING="$(mktemp -d)"
RW_DMG="build/${APP_NAME}-rw.dmg"
rm -f "$RW_DMG"

cp -R "dist/${APP_NAME}.app" "${STAGING}/${APP_NAME}.app"
ln -s /Applications "${STAGING}/Applications"
mkdir -p "${STAGING}/.background"
cp "assets/dmg_background.png" "${STAGING}/.background/background.png"
cp "assets/CynoType.icns" "${STAGING}/.VolumeIcon.icns"

# Create temporary read-write DMG
hdiutil create \
  -volname "${VOL_NAME}" \
  -srcfolder "${STAGING}" \
  -ov -format UDRW \
  "$RW_DMG" >/dev/null

# Mount and configure clean Apple Finder installer layout
MOUNT_OUT="$(hdiutil attach -readwrite -noverify -noautoopen "$RW_DMG")"
MOUNT_DIR="$(echo "$MOUNT_OUT" | awk -F'\t' '/\/Volumes\// {print $NF; exit}')"
DEV_NODE="$(echo "$MOUNT_OUT" | awk '/^\/dev\// {print $1; exit}')"

if [ -n "$MOUNT_DIR" ]; then
  # Enable custom volume icon flag if SetFile is available
  if command -v SetFile >/dev/null 2>&1; then
    SetFile -a C "$MOUNT_DIR" 2>/dev/null || true
  fi

  # Apply Finder window layout (best-effort if Finder GUI scripting is available)
  osascript << APPLESCRIPT 2>/dev/null || true
tell application "Finder"
  tell disk "${VOL_NAME}"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {240, 180, 900, 600}
    set viewOptions to the icon view options of container window
    set arrangement of viewOptions to not arranged
    set icon size of viewOptions to 108
    set background picture of viewOptions to file ".background:background.png"
    set position of item "${APP_NAME}.app" of container window to {180, 200}
    set position of item "Applications" of container window to {480, 200}
    update without registering applications
    delay 1
    close
  end tell
end tell
APPLESCRIPT

  sync
  hdiutil detach "$DEV_NODE" -quiet || hdiutil detach "$MOUNT_DIR" -force -quiet
fi

rm -rf "$STAGING"

# Convert to final compressed read-only DMG
hdiutil convert "$RW_DMG" -format UDZO -imagekey zlib-level=9 -o "dist/${DMG_NAME}" >/dev/null
rm -f "$RW_DMG"

echo ""
echo "============================================================"
echo " Build complete!"
echo "   App Bundle : dist/${APP_NAME}.app"
echo "   Installer  : dist/${DMG_NAME}"
echo "============================================================"
