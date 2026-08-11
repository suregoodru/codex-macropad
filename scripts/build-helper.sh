#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
BUILD_PATH="$PROJECT_ROOT/build"
SWIFT_CACHE_PATH="/private/tmp/codex-macropad-swiftpm-cache"
CLANG_CACHE_PATH="/private/tmp/codex-macropad-clang-cache"
SWIFTPM_SUPPORT_PATH="$PROJECT_ROOT/.swiftpm-cache"

mkdir -p \
    "$BUILD_PATH" \
    "$SWIFTPM_SUPPORT_PATH/cache" \
    "$SWIFTPM_SUPPORT_PATH/config" \
    "$SWIFTPM_SUPPORT_PATH/security"

CLANG_MODULE_CACHE_PATH="$CLANG_CACHE_PATH" \
SWIFTPM_MODULECACHE_OVERRIDE="$SWIFT_CACHE_PATH" \
    swift build \
        --disable-sandbox \
        --disable-build-manifest-caching \
        --cache-path "$SWIFTPM_SUPPORT_PATH/cache" \
        --config-path "$SWIFTPM_SUPPORT_PATH/config" \
        --security-path "$SWIFTPM_SUPPORT_PATH/security" \
        --configuration release \
        --scratch-path "$PROJECT_ROOT/.build" \
        --product MacropadDisplay

cp "$PROJECT_ROOT/.build/release/MacropadDisplay" "$BUILD_PATH/MacropadDisplay"
chmod 755 "$BUILD_PATH/MacropadDisplay"
