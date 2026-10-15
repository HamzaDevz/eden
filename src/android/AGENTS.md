# src/android — carte locale (branche odin3)

App Android : UI Kotlin, pont JNI vers le cœur C++, chargement de pilotes GPU custom, build de l'APK. Chemins relatifs à la racine, lignes au commit `e255c20db`. Abréviations : `KT/` = `src/android/app/src/main/java/org/yuzu/yuzu_emu/`, `JNI/` = `src/android/app/src/main/jni/`.

## JNI : démarrage d'une partie

- `EmulationSession::InitializeEmulation` `src/android/app/src/main/jni/native.cpp:282` : `ApplySettings`/`LogSettings` `:295-296`, `m_system.Load` `:334`, `GPU().Start()` `:340`.
- `RunEmulation` `src/android/app/src/main/jni/native.cpp:406` : rejoue le cache disque de shaders avant `Run` (`:413-420`) ; `ReloadDiskShaderCache` `:456`.
- Stats pour l'overlay : `getPerfStats` `src/android/app/src/main/jni/native.cpp:992-1006` (`double[4]` = fps système, fps jeu, frametime, vitesse), `getShadersBuilding` `:1008-1016`, `getCpuBackend` `:1018`, `getGpuDriver` `:1026`.
- Réglages propres à Android : `src/android/app/src/main/jni/android_settings.h` (`driver_path` par jeu `:54`, `pipeline_worker_count` par jeu, défaut 2 `:150`).

## Pilotes GPU (libadrenotools)

- Ordre au lancement de l'app : config Freedreno puis `GpuDriverHelper.initializeDriverParameters` (`src/android/app/src/main/java/org/yuzu/yuzu_emu/YuzuApplication.kt:68-71`, `KT/utils/GpuDriverHelper.kt:36`).
- `EmulationSession::InitializeGpuDriver` `src/android/app/src/main/jni/native.cpp:162-197` : `adrenotools_open_libvulkan(... ADRENOTOOLS_DRIVER_CUSTOM ...)` `:179` si un pilote custom est choisi, sinon pilote système `:186`. Redirection de fichiers seulement avec `renderer_debug`.
- Support pilote custom = API ≥ 28 et `/dev/kgsl-3d0` présent (`src/android/app/src/main/jni/native.cpp:838-856`).
- Pilote par jeu : `driver_path` est `SwitchableSetting` ; `DriverViewModel.onDriverSelected` (`src/android/app/src/main/java/org/yuzu/yuzu_emu/model/DriverViewModel.kt:114`) vide le cache shader du jeu, `onLaunchGame` `:219` installe le zip choisi (appelé `KT/fragments/EmulationFragment.kt:1051`).
- Variables Freedreno/Turnip (`TU_DEBUG`, `FD_DEV_FEATURES`…) : `src/android/app/src/main/jni/native_freedreno.cpp` (fichiers `.freedreno.conf` et `freedreno_games/` `:40-41`), chargées par jeu `src/android/app/src/main/java/org/yuzu/yuzu_emu/fragments/EmulationFragment.kt:351-357`. Elles ne concernent que Mesa Turnip, donc rien pour un Adreno 830 sur pilote Qualcomm stock.

## Réglages par jeu côté Kotlin

- Lancement avec config custom : `src/android/app/src/main/java/org/yuzu/yuzu_emu/fragments/EmulationFragment.kt:326-335` (`SettingsFile.loadCustomConfig`, sinon `NativeConfig.reloadGlobalConfig`).
- Fichier `%016X.ini` : `src/android/app/src/main/jni/native_config.cpp:75`, chargement `:70-91`.
- Android n'appelle jamais `Settings::RestoreGlobalState` : le retour au global passe par un rechargement de l'INI global.
- Écran des réglages : `KT/features/settings/ui/SettingsFragmentPresenter.kt` (workers `:562`, CPU `:1529-1533`, debug `:1523-1558`).

## Overlay de performance

`KT/fragments/EmulationFragment.kt:1525-1652`, rafraîchi toutes les 800 ms : fps jeu, frametime, RAM, température batterie, shaders en compilation. La vitesse d'émulation est calculée mais pas affichée. Toggles : `KT/features/settings/model/BooleanSetting.kt:72-81`.

## Build de l'APK depuis ce Mac

- Versions : AGP 8.9.1 (`src/android/build.gradle.kts:6`), Gradle 8.13, JDK 17 (21 accepté), compileSdk 36 et NDK `28.2.13676358` (`src/android/app/build.gradle.kts:38-39`), CMake 3.31.6 (`src/android/app/build.gradle.kts:262`), arm64-v8a seulement. `docs/build/Android.md` est en partie périmé (NDK 27, CMake 3.22).
- Arguments CMake `src/android/app/build.gradle.kts:79-93` (dépendances bundlées via CPMUtil) ; arguments en plus via `-PYUZU_ANDROID_ARGS="..."` (`:75-77`).
- Flavors (dimension `version`, `src/android/app/build.gradle.kts:186`) :
  - `mainline` = Standard, minSdk 33, `dev.eden.eden_emulator` (`src/android/app/build.gradle.kts:188-199`).
  - `genshinSpoof` = « Optimized », minSdk 35, package `com.miHoYo.Yuanshen`, `-DGENSHIN_SPOOF=ON` (`src/android/app/build.gradle.kts:201-217`). Ce define ne sert qu'à choisir `optimized.apk` pour la mise à jour (`src/common/net/net.cpp:86-87`) : aucune option de compilation ne diffère. Le seul effet runtime possible est le nom de package, que l'OS ou le pilote peuvent reconnaître comme un jeu connu (hypothèse non vérifiée).
  - `legacy`, minSdk 29, `-DYUZU_LEGACY=ON` (`src/android/app/build.gradle.kts:219-241`) : seuils de cache réduits (`src/video_core/buffer_cache/buffer_cache_base.h:176`, `src/video_core/texture_cache/texture_cache_base.h:115`).
- Build types : `release` (`src/android/app/build.gradle.kts:131-151`), `relWithDebInfo` (`src/android/app/build.gradle.kts:155-169`, débogable, suffixe `.relWithDebInfo`), `debug` (`src/android/app/build.gradle.kts:173-181`, suffixe `.debug`). Les suffixes s'installent à côté de l'app officielle mais cassent le nom de package spoofé de `genshinSpoof`.
- Aucune flavor ne change `-march`, LTO ou `-O` ; `YUZU_BUILD_PRESET` (`CMakeLists.txt:346-365`, défaut `custom`) le permet, ex. `-PYUZU_ANDROID_ARGS="-DYUZU_BUILD_PRESET=optimized"` (`armv8.2-a+lse+rcpc`). Éviter `armv9` sur Snapdragon 8 Elite : les cœurs Oryon sont censés ne pas avoir SVE, donc risque de SIGILL (à confirmer avec `adb shell grep -m1 Features /proc/cpuinfo`).
- Signature : clés via `ANDROID_KEYSTORE_FILE`/`ANDROID_KEYSTORE_PASS`/`ANDROID_KEY_ALIAS` (`src/android/app/build.gradle.kts:107-116`), sinon le `debug.keystore` du dépôt. Un APK signé avec cette clé ne peut pas remplacer une version officielle déjà installée sous le même package.
- Prérequis manquants sur ce Mac : `sdkmanager` (cmdline-tools), `platforms;android-36`, `ndk;28.2.13676358`, `cmake;3.31.6`, et `glslangValidator` (`brew install glslang`, requis par `src/video_core/host_shaders/CMakeLists.txt:101-105`). CPMUtil a besoin du réseau à chaque configure (`CMakeModules/CPMUtil.cmake:1048-1060`).
- Commande minimale :

```sh
export JAVA_HOME=$(/usr/libexec/java_home -v 21)
export ANDROID_HOME=~/Library/Android/sdk
cd src/android && ./gradlew assembleGenshinSpoofRelWithDebInfo
```

Ou depuis la racine : `.ci/android/build.sh -t optimized -b RelWithDebInfo` (cibles `.ci/android/build.sh:79-85`). Les APK sont copiés dans `artifacts/` (`src/android/app/build.gradle.kts:392-421`).
