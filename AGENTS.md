# AGENTS

Read and follow the AI policy found in `docs/policies/AI.md`, and the coding policies found in `docs/policies/Coding.md`.

Do not, under any circumstances, ever submit PRs or push commits on behalf of the user.

Keep comments to a reasonable level. Do not overexplain or add/remove comments from unrelated parts of the codebase.

---

## Carte locale (branche odin3, hors upstream)

Index de navigation ajouté localement, en français. Chemins relatifs à la racine ; les numéros de ligne datent du commit `e255c20db` et dérivent : revérifier avant de citer. Commits sur `odin3` avec un trailer `Assisted-by:`, jamais `Co-authored-by:` ni `Signed-off-by:` (`docs/policies/AI.md`).

`odin3` est notre main : remote `origin` = notre fork `HamzaDevz/eden` (GitHub), `upstream` = l'amont officiel, `master` = miroir de `upstream/master`. Exception voulue par Hamza à l'interdiction ci-dessus : tout travail se commite sur `odin3` et se pousse sur `origin` (jamais vers `upstream`, jamais de PR), puis l'APK est recompilée depuis `odin3` et installée sur la console.

- `src/common/AGENTS.md` : système de réglages (global / par jeu), placement des threads, ADPF.
- `src/core/AGENTS.md` : cycle de vie `System`, nvdrv → GPU, présentation, overrides par titre.
- `src/dynarmic/AGENTS.md` : JIT CPU, glue `src/core/arm`, NCE, options Unsafe.
- `src/video_core/AGENTS.md` : renderer Vulkan, spécificités Adreno/Qualcomm, cache de pipelines.
- `src/shader_recompiler/AGENTS.md` : Maxwell → IR → SPIR-V, profil hôte, contournements Qualcomm.
- `src/android/AGENTS.md` : JNI, pilotes custom, réglages par jeu Kotlin, build de l'APK depuis ce Mac.
- `docs/Odin3.md` : par où commencer pour optimiser un jeu précis sur Adreno 830, profilage, banc de test adb.
