# src/dynarmic — carte locale (branche odin3)

JIT ARMv8/ARMv7 → hôte (backends x64 et arm64). La glue Eden est dans `src/core/arm/`. Sur Android arm64 le backend par défaut est NCE (exécution native), Dynarmic sert de repli. Chemins relatifs à la racine, lignes au commit `e255c20db`.

## Choix du backend

- `HAS_NCE` défini `CMakeLists.txt:302-305`, sources NCE `src/core/CMakeLists.txt:1232-1246`.
- Choix par processus : `src/core/hle/kernel/k_process.cpp:1309-1328`. `cpu_backend` `src/common/settings.h:252-265` (Nce si `HAS_NCE`).
- NCE exige fastmem, NCE sélectionné et un programme 39 bits (`SetNceEnabled` `src/common/settings.cpp:199-211`) ; fastmem exige des pages de 4 KiB sur Linux arm64 (`IsFastmemEnabled` `:181-195`). Activé au chargement depuis `src/core/loader/deconstructed_rom_directory.cpp:185-189`.

## Configuration du JIT (`src/core/arm/dynarmic/arm_dynarmic_64.cpp`, `MakeJit`)

- Page table `:199-209`, fastmem `:211-215`, exclusives `:217-218`, moniteur global `:229-230`, `wall_clock` `:243-244`, cache de code 128 MiB `:247-248`.
- Branches par `cpu_accuracy` dans ce même fichier : Debugging `:266-304`, Unsafe `:306-327`, Auto `:329-334` (unsafe + UnfuseFMA, `fastmem_address_space_bits=64`, IgnoreGlobalMonitor), Paranoid `:336`, Accurate `:340` ; fastmem coupé si désactivé `:344-347`.
- Réglages lus : `cpu_accuracy` `src/common/settings.h:266` (enum `src/common/settings_enums.h:142`), `cpuopt_*` pour Debugging `src/common/settings.h:295-313`, `cpuopt_unsafe_*` pour Unsafe `src/common/settings.h:315-334`.
- Équivalent 32 bits : `src/core/arm/dynarmic/arm_dynarmic_32.cpp` (`MakeJit` `:150`, Auto `:275-280`).

## Ce que le backend arm64 honore réellement

- Seuls `BlockLinking` (`src/dynarmic/src/dynarmic/backend/arm64/emit_arm64_a64.cpp:46`, `:65`) et `ReturnStackBuffer` (`src/dynarmic/src/dynarmic/backend/arm64/emit_arm64.cpp:52`) sont lus.
- Tous les `Unsafe_*`, `FastDispatch`, `MiscIROpt` et `CodeSpeed` n'ont d'effet que sur x64 : sur l'Odin 3, les cases « Unsafe » de l'UI ne changent rien au code généré.
- La vraie différence Auto vs Accurate sur arm64 : pas de contrôle de bornes fastmem quand l'espace d'adressage vaut 64 bits (`src/dynarmic/src/dynarmic/backend/arm64/emit_arm64_memory.cpp:525-526`), sinon LSR + CBNZ (`:534-535`).
- `cpuopt_fastmem_exclusives` sans effet sur arm64 (`src/dynarmic/src/dynarmic/backend/arm64/a64_address_space.cpp:566-600`).
- Cache de code vidé entièrement quand il reste moins de 1 MiB (`src/dynarmic/src/dynarmic/backend/arm64/address_space.cpp:110-111`).

## Interface et passes

- `UserConfig` `src/dynarmic/src/dynarmic/interface/A64/config.h:134` (`optimizations` `:165`, `fastmem_address_space_bits` `:208`, `code_cache_size` `:212`, `unsafe_optimizations` `:228`).
- Drapeaux : `src/dynarmic/src/dynarmic/interface/optimization_flags.h` (FastDispatch `:28`, CodeSpeed `:39`, Unsafe `:45-58`).
- Passes IR : `src/dynarmic/src/dynarmic/ir/opt_passes.cpp`, pipeline A64 `:1464-1480`.
- Conception : `docs/dynarmic/` (LinkBlock, RSB, fastmem).

## NCE (`src/core/arm/nce/`)

- Patcher : `src/core/arm/nce/patcher.cpp:50-183` (SVC, TPIDR, CNTPCT, CNTFRQ).
- Signaux : `src/core/arm/nce/arm_nce.cpp:279-335` ; SIGSEGV → `InvalidateNCE` `src/core/memory.cpp:964-985`.
- CNTPCT patché ⇒ `cpu_clock` probablement sans effet sous NCE (déduction).

## Pièges

- Avant d'optimiser le JIT, vérifier que le jeu tourne vraiment sous NCE (log au chargement, ou `getCpuBackend` JNI `src/android/app/src/main/jni/native.cpp:1018`) : avec des pages de 16 KiB fastmem et NCE tombent, et la perf CPU chute.
- Dynarmic est un sous-projet vendu dans l'arbre : garder ses conventions et ne pas mélanger ses changements avec ceux d'Eden dans un même commit.
