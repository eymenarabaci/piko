"""
Build script to compile, package, and sign Agent Remote Android APK locally
using installed Android SDK tools and Java 17.
"""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# Portable Auto-detection of Android SDK
sdk_env = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
if sdk_env and Path(sdk_env).is_dir():
    ANDROID_SDK = Path(sdk_env)
else:
    default_sdk = Path.home() / "AppData" / "Local" / "Android" / "Sdk"
    ANDROID_SDK = default_sdk if default_sdk.is_dir() else Path("/usr/lib/android-sdk")

# Find latest build tools
bt_dir = ANDROID_SDK / "build-tools"
if bt_dir.is_dir() and list(bt_dir.iterdir()):
    BUILD_TOOLS = sorted(list(bt_dir.iterdir()))[-1]
else:
    BUILD_TOOLS = bt_dir / "33.0.0"

# Find latest platform android.jar
plat_dir = ANDROID_SDK / "platforms"
if plat_dir.is_dir() and list(plat_dir.iterdir()):
    PLATFORM_JAR = sorted(list(plat_dir.iterdir()))[-1] / "android.jar"
else:
    PLATFORM_JAR = plat_dir / "android-33" / "android.jar"

# Portable Java detection
java_home = os.environ.get("JAVA_HOME")
if java_home and Path(java_home).is_dir():
    JAVA_BIN = Path(java_home) / "bin"
elif shutil.which("javac"):
    JAVA_BIN = Path(shutil.which("javac")).parent
else:
    # Standard Eclipse Adoptium / OpenJDK fallback
    cand = list(Path(r"C:\Program Files\Eclipse Adoptium").glob("jdk-*")) if Path(r"C:\Program Files\Eclipse Adoptium").is_dir() else []
    JAVA_BIN = (cand[0] / "bin") if cand else Path(r"C:\Program Files\Java\jdk-17\bin")

ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build_temp"
GEN_DIR = BUILD_DIR / "gen"
BIN_DIR = BUILD_DIR / "bin"
OUTPUT_APK = ROOT / "AgentRemote.apk"
DIST_APK = ROOT.parent / "agent-manager" / "ui" / "AgentRemote.apk"


def run(cmd, desc):
    print(f"[BUILD] {desc}...")
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[ERROR] {desc} failed (exit {res.returncode}):")
        if res.stdout:
            print(res.stdout)
        if res.stderr:
            print(res.stderr)
        sys.exit(res.returncode)
    return res


def main():
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    BIN_DIR.mkdir(parents=True, exist_ok=True)

    aapt2 = BUILD_TOOLS / ("aapt2.exe" if os.name == 'nt' else "aapt2")
    d8_jar = BUILD_TOOLS / "lib" / "d8.jar"
    zipalign = BUILD_TOOLS / ("zipalign.exe" if os.name == 'nt' else "zipalign")
    apksigner_jar = BUILD_TOOLS / "lib" / "apksigner.jar"
    java = JAVA_BIN / ("java.exe" if os.name == 'nt' else "java")
    javac = JAVA_BIN / ("javac.exe" if os.name == 'nt' else "javac")
    keytool = JAVA_BIN / ("keytool.exe" if os.name == 'nt' else "keytool")

    # Step 1: aapt2 compile
    compiled_res = BUILD_DIR / "compiled_res.zip"
    run(f'"{aapt2}" compile --dir "{ROOT / "res"}" -o "{compiled_res}"', "1. AAPT2 Resources Compile")

    # Step 2: aapt2 link
    base_apk = BUILD_DIR / "base.apk"
    run(
        f'"{aapt2}" link -o "{base_apk}" -I "{PLATFORM_JAR}" '
        f'--manifest "{ROOT / "AndroidManifest.xml"}" "{compiled_res}" '
        f'-A "{ROOT / "assets"}" --java "{GEN_DIR}" --auto-add-overlay',
        "2. AAPT2 Link Manifest & Assets"
    )

    # Step 3: compile java sources
    r_java = list(GEN_DIR.glob("**/R.java"))
    src_java = list((ROOT / "src").glob("**/*.java"))
    all_java = [f'"{str(p)}"' for p in (r_java + src_java)]

    run(
        f'"{javac}" -source 1.8 -target 1.8 -cp "{PLATFORM_JAR}" -d "{BIN_DIR}" ' + " ".join(all_java),
        "3. Java Compile (MainActivity & WebAppInterface)"
    )

    # Step 4: d8 compile classes to dex
    class_files = list(BIN_DIR.glob("**/*.class"))
    classes_arg = " ".join(f'"{str(p)}"' for p in class_files)
    run(
        f'"{java}" -cp "{d8_jar}" com.android.tools.r8.D8 --lib "{PLATFORM_JAR}" '
        f'--output "{BUILD_DIR}" {classes_arg}',
        "4. D8 Dex Compilation"
    )

    # Step 5: add classes.dex to base.apk
    print("[BUILD] 5. Adding classes.dex to base.apk...")
    dex_file = BUILD_DIR / "classes.dex"
    with zipfile.ZipFile(base_apk, 'a') as zf:
        zf.write(dex_file, arcname="classes.dex")

    # Step 6: zipalign
    aligned_apk = BUILD_DIR / "aligned.apk"
    run(f'"{zipalign}" -f -v 4 "{base_apk}" "{aligned_apk}"', "6. Zipalign APK")

    # Step 7: generate debug keystore if missing
    keystore = ROOT / "debug.keystore"
    if not keystore.exists():
        run(
            f'"{keytool}" -genkey -v -keystore "{keystore}" -storepass android -alias androiddebugkey '
            f'-keypass android -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=AndroidDebug,O=Android,C=US"',
            "7. Generating Debug Keystore"
        )

    # Step 8: sign apk
    run(
        f'"{java}" -jar "{apksigner_jar}" sign --ks "{keystore}" '
        f'--ks-pass pass:android --ks-key-alias androiddebugkey --key-pass pass:android '
        f'--out "{OUTPUT_APK}" "{aligned_apk}"',
        "8. Apksigner Signing APK"
    )

    # Also copy to agent-manager/ui for web download
    if DIST_APK.parent.exists():
        shutil.copy2(OUTPUT_APK, DIST_APK)

    # Cleanup temp
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)

    size_kb = round(OUTPUT_APK.stat().st_size / 1024, 1)
    print("")
    print("=" * 60)
    print(" [OK] Agent Remote APK Basariyla Uretildi!")
    print(f" Cikti APK: {OUTPUT_APK}")
    print(f" Web UI Dagitim APK: {DIST_APK}")
    print(f" Boyut: {size_kb} KB")
    print("=" * 60)


if __name__ == "__main__":
    main()
