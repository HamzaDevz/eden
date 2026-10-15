# src/shader_recompiler — carte locale (branche odin3)

Traduit les shaders Maxwell invités en IR puis en SPIR-V (Vulkan), GLSL ou GLASM (OpenGL). Appelé par `src/video_core/renderer_vulkan/vk_pipeline_cache.cpp`. Chemins relatifs à la racine, lignes au commit `e255c20db`.

## Flux

1. `TranslateProgram` `src/shader_recompiler/frontend/maxwell/translate_program.cpp:331` : CFG puis blocs IR (`:337-339`).
2. Passes (ordre dans `TranslateProgram`) : lowerings conditionnels Fp64 `:377`, Fp16 `:380`, Int64 `:383`, ConditionalBarrier `:386` ; puis SsaRewrite `:389`, ConstantPropagation `:391`, Position `:393`, GlobalMemoryToStorageBuffer `:395`, GeometryCompaction `:396`, Texture `:397`, Rescaling si mise à l'échelle active `:399-400`, DeadCodeElimination `:402`, Verification si `renderer_debug` `:404-405`, CollectShaderInfo `:407`, Layer `:408`, VendorWorkaround `:409`. Déclarations : `src/shader_recompiler/ir_opt/passes.h:18-41`.
3. Cas particuliers : `MergeDualVertexPrograms` `translate_program.cpp:416`, `ConvertLegacyToGeneric` `:449`, `GenerateGeometryPassthrough` `:509`.
4. Émission : `EmitSPIRV` `src/shader_recompiler/backend/spirv/emit_spirv.cpp:553` (sirit ; seule post-passe : réécriture d'opcode `:566`). Pas de spirv-opt dans le build.

## Entrées hôte

- `Profile` `src/shader_recompiler/profile.h:15-120`, `HostTranslateInfo` `src/shader_recompiler/host_translate_info.h:17-66`, `RuntimeInfo` `src/shader_recompiler/runtime_info.h:85-117`.
- Remplis côté Vulkan : `profile` `src/video_core/renderer_vulkan/vk_pipeline_cache.cpp:379-468`, `host_info` `:470-495`, `MakeRuntimeInfo` `:156-304`.

## Contournements Qualcomm (pilote stock)

- `has_broken_spirv_position_input` `vk_pipeline_cache.cpp:453` → Position encapsulée dans une struct (`src/shader_recompiler/backend/spirv/spirv_emit_context.cpp:1504`).
- `has_broken_fp32_denorm_flush` `vk_pipeline_cache.cpp:457` → mode DenormFlushToZero non déclaré (`src/shader_recompiler/backend/spirv/emit_spirv.cpp:344`).
- `has_broken_spirv_subgroup_mask_vector_extract_dynamic` pour pilote < 512.672.0 `vk_pipeline_cache.cpp:459-461` → chaîne de select (`src/shader_recompiler/backend/spirv/emit_spirv_warp.cpp:30`).
- `has_broken_spirv_subgroup_shuffle` `vk_pipeline_cache.cpp:462` → boucle de broadcast sur `max_subgroup_size` (`emit_spirv_warp.cpp:94-98`), coûteux dans les shaders qui shufflent.
- Formats scaled émulés (`support_scaled_attributes` faux, `vk_pipeline_cache.cpp:430`, émulation `spirv_emit_context.cpp:210-235`) et Int64 abaissé car `shaderInt64` retiré côté device.
- Turnip seulement : subgroups retirés de l'étage géométrie `vk_pipeline_cache.cpp:376-378`.
- `HasBrokenPattern` `src/shader_recompiler/ir_opt/vendor_workaround_pass.cpp:82` : le shader est purement sauté sur tout pilote non NVIDIA (`vk_pipeline_cache.cpp:841-843`), source possible d'effets manquants.
- Hack Android `fix_bloom_effects` : multiplie chaque échantillon float par 0.98 (`src/shader_recompiler/backend/spirv/emit_spirv_image.cpp:593-597`).

## Points chauds et pièges

- La traduction tourne sur le thread GPU lors d'un miss en jeu : c'est elle qui fait les saccades, pas seulement `vkCreateGraphicsPipelines`.
- Le cache disque stocke le code invité, pas le SPIR-V : chaque boot retraduit tout le cache.
- Changer une passe ou un champ de `Profile` change le SPIR-V mais pas la clé du cache disque : le cache pilote se reconstruit, le cache Eden reste valide.
- Dump des shaders invités : `dump_guest_shaders` (`src/common/settings.h:882`, utilisé `vk_pipeline_cache.cpp:846`) ; dump SPIR-V via `gpu_log_shader_dumps` (`vk_pipeline_cache.cpp:878-893`).
